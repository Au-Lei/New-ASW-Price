"""Fill a sanitized copy of the supplied layout using the app's OOXML exporter.

The source template's headers, styles, widths and panes are retained. Catalog
examples are removed from the shipped asset; exported rows are published data only.
"""
from copy import deepcopy
from datetime import date,datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as E
from zipfile import ZipFile,ZIP_DEFLATED
from .excel_format import MAIN,REL,NS,column,safe_text
from .port_groups import grouped,merge_ranges

TEMPLATE=Path(__file__).resolve().parents[1]/'assets/quotation-template.xlsx'
CT='http://schemas.openxmlformats.org/package/2006/content-types'
PKG='http://schemas.openxmlformats.org/package/2006/relationships'

def xml_bytes(root):
    # OPC consumers require unprefixed package roots (Types / Relationships).
    data=E.tostring(root,encoding='utf-8',xml_declaration=True)
    import re
    namespace=root.tag.split('}')[0][1:]
    match=re.search(rb'xmlns:([A-Za-z0-9_]+)="'+re.escape(namespace.encode())+rb'"',data)
    if match:
        prefix=match.group(1)
        data=data.replace(b'xmlns:'+prefix+b'=',b'xmlns=')
        data=data.replace(b'<'+prefix+b':',b'<').replace(b'</'+prefix+b':',b'</')
    return data


def write_template(sheets):
    with ZipFile(TEMPLATE) as original:
        files={name:original.read(name) for name in original.namelist()}
    book=E.fromstring(files['xl/workbook.xml']);nodes=book.find('x:sheets',NS)
    relations=E.fromstring(files['xl/_rels/workbook.xml.rels'])
    content=E.fromstring(files['[Content_Types].xml'])
    specs=[];by_name={};by_group={}
    for index,node in enumerate(nodes,1):
        spec={'name':node.get('name'),'file':f'xl/worksheets/sheet{index}.xml','rows':[],'port_groups':[],'origin':'青岛'}
        specs.append(spec);by_name[spec['name']]=spec
    default_root=files[specs[0]['file']]
    for sheet in sheets:
        for row,key in zip(sheet['rows'],sheet['port_groups']):
            origin=row[18] or '起运港未填写'
            region=sheet['name']
            # Keep the six original Qingdao tabs and give other origins their own tabs.
            group=(region,origin)
            name=by_group.get(group,region if origin.upper()=='QINGDAO' else origin+'-'+region)
            if name not in by_name:
                import re
                base=re.sub(r'[\\/*?:\[\]]','_',name).strip(" '")[:31] or '其他航线'
                name=base;suffix=1
                while name in by_name:
                    suffix+=1;name=base[:26]+'_'+str(suffix)
                number=len(specs)+1;file=f'xl/worksheets/sheet{number}.xml';rid=f'exportSheet{number}'
                E.SubElement(nodes,f'{{{MAIN}}}sheet',name=name,sheetId=str(number),attrib={f'{{{REL}}}id':rid})
                E.SubElement(relations,f'{{{PKG}}}Relationship',Id=rid,Type=REL+'/worksheet',Target=f'worksheets/sheet{number}.xml')
                E.SubElement(content,f'{{{CT}}}Override',PartName='/'+file,ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
                by_name[name]={'name':name,'file':file,'rows':[],'port_groups':[],'origin':origin};specs.append(by_name[name])
                files[file]=default_root
            by_group[group]=name
            by_name[name]['rows'].append(row[:18])
            by_name[name]['port_groups'].append(key)
    for spec in specs:
        root=E.fromstring(files[spec['file']]);data=root.find('x:sheetData',NS)
        prototype=deepcopy(data[2]);styles=[c.get('s','0') for c in prototype]
        for row in list(data)[2:]:data.remove(row)
        # Original title already identifies Qingdao; new origin tabs get accurate titles.
        if spec['origin']!='青岛':
            title=data[0][0]
            for child in list(title):title.remove(child)
            title.set('t','inlineStr');E.SubElement(E.SubElement(title,f'{{{MAIN}}}is'),f'{{{MAIN}}}t').text=spec['origin']+'港到'+spec['name'].split('-',1)[-1]+'报价表'
        pairs=sorted(zip(spec['rows'],spec['port_groups']),key=lambda p:tuple(str(p[0][i] or '') for i in (0,1,3,5)))
        rows,keys=grouped([r for r,k in pairs],[k for r,k in pairs])
        port_ranges=merge_ranges(keys,3)
        covered=set()
        for ref in port_ranges:
            first,last=ref.split(':');col=first[0]
            covered.update(f'{col}{n}' for n in range(int(first[1:])+1,int(last[1:])+1))
        for index,values in enumerate(rows,3):
            row=E.SubElement(data,f'{{{MAIN}}}row',r=str(index),ht='36',customHeight='1')
            for col,value in enumerate(values,1):
                cell=E.SubElement(row,f'{{{MAIN}}}c',r=f'{column(col)}{index}',s=styles[col-1])
                if value is None or value=='' or f'{column(col)}{index}' in covered:continue
                if isinstance(value,(date,datetime)):
                    day=value.date() if isinstance(value,datetime) else value
                    E.SubElement(cell,f'{{{MAIN}}}v').text=str((day-date(1899,12,30)).days)
                elif isinstance(value,(Decimal,int)) and not isinstance(value,bool):
                    E.SubElement(cell,f'{{{MAIN}}}v').text=str(value)
                else:
                    cell.set('t','inlineStr');E.SubElement(E.SubElement(cell,f'{{{MAIN}}}is'),f'{{{MAIN}}}t',attrib={'xml:space':'preserve'}).text=safe_text(value)
        merges=root.find('x:mergeCells',NS)
        # Merge consecutive route labels, as the source template does.
        start=0
        while start<len(rows):
            stop=start+1
            while stop<len(rows) and rows[stop][0]==rows[start][0]:stop+=1
            if rows[start][0] and stop-start>1:
                E.SubElement(merges,f'{{{MAIN}}}mergeCell',ref=f'A{start+3}:A{stop+2}')
                for number in range(start+4,stop+3):
                    cell=data[number-1][0]
                    for child in list(cell):cell.remove(child)
                    cell.attrib.pop('t',None)
            start=stop
        for ref in port_ranges:E.SubElement(merges,f'{{{MAIN}}}mergeCell',ref=ref)
        merges.set('count',str(len(merges)))
        for validation in root.findall('x:dataValidations/x:dataValidation',NS):validation.set('sqref',f'J3:J{max(3,len(rows)+2)}')
        files[spec['file']]=xml_bytes(root)
    files['xl/workbook.xml']=xml_bytes(book)
    files['xl/_rels/workbook.xml.rels']=xml_bytes(relations)
    files['[Content_Types].xml']=xml_bytes(content)
    result=BytesIO()
    with ZipFile(result,'w',ZIP_DEFLATED) as z:
        for name,value in files.items():z.writestr(name,value)
    return result.getvalue()
