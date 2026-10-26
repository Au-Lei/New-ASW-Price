"""Bounded OOXML reader/writer for the fixed quotation layout; no macros or formulas run."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
import posixpath
import re
from xml.etree import ElementTree as E
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

MAIN='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS={'x':MAIN}
HEADERS=['航线','国家英文名','国家中文名','英文港口名','中文港口名','船公司','20GP','40GP','40HQ','货币','船期','航程天数','途径路径','价格来源','备注（注意事项）','有效期起','有效期止','费用口径']
EXTRAS=['起运港','有效期依据','已含费用','另计费用','待确认费用','适用条件','服务标识','服务类型','报价供应商','协议／报价系列','报价编号','版本','状态']
MAX_FILE=20*1024*1024


def column(index):
    result=''
    while index:
        index,remainder=divmod(index-1,26)
        result=chr(65+remainder)+result
    return result


def column_number(text):
    result=0
    for char in text:
        result=result*26+ord(char)-64
    return result


def read_xlsx(content,max_file=MAX_FILE):
    if len(content)>max_file:
        raise ValueError('Excel超过允许文件大小')
    from .legacy_excel import OLE_MAGIC, read_xls
    if content.startswith(OLE_MAGIC):return read_xls(content)
    try:
        z=ZipFile(BytesIO(content))
    except BadZipFile as exc:
        raise ValueError('不是有效的Excel文件，内容损坏或并非xls/xlsx；请另存为未加密xlsx') from exc
    with z:
        entries=z.infolist()
        if len(entries)>2000 or sum(e.file_size for e in entries)>100*1024*1024:
            raise ValueError('Excel解压内容超限')
        if len({entry.filename for entry in entries})!=len(entries):
            raise ValueError('Excel包含重复的内部文件')
        if any(entry.flag_bits & 1 for entry in entries):
            raise ValueError('不支持加密的Excel文件，请另存为未加密xlsx')
        if any('vbaproject' in name.lower() or name.lower().startswith('xl/externallinks/') for name in z.namelist()):
            raise ValueError('不支持含宏或外部工作簿链接的文件')
        def xml(member):
            raw=z.read(member)
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
                raise ValueError('Excel包含不支持的XML声明')
            return E.fromstring(raw)
        try:
            book=xml('xl/workbook.xml')
            props=book.find('x:workbookPr',NS)
            is1904=props is not None and props.get('date1904') in ('1','true')
            shared=[''.join(node.itertext()) for node in xml('xl/sharedStrings.xml').findall('x:si',NS)] if 'xl/sharedStrings.xml' in z.namelist() else []
            date_styles=set()
            if 'xl/styles.xml' in z.namelist():
                styles=xml('xl/styles.xml')
                formats={int(n.get('numFmtId')):n.get('formatCode','') for n in styles.findall('x:numFmts/x:numFmt',NS)}
                for index,style in enumerate(styles.findall('x:cellXfs/x:xf',NS)):
                    number=int(style.get('numFmtId','0'))
                    code=re.sub(r'"[^"]*"|\\.|\[[^\]]*\]','',formats.get(number,'')).lower()
                    if number in (*range(14,23),*range(45,48)) or ('y' in code and ('d' in code or 'm' in code)):
                        date_styles.add(index)
            relations={r.get('Id'):r for r in xml('xl/_rels/workbook.xml.rels')}
            result=[]
            for sheet in book.findall('x:sheets/x:sheet',NS):
                relation=relations[sheet.get('{'+REL+'}id')]
                if relation.get('TargetMode')=='External':
                    raise ValueError('不支持外部工作表')
                target=relation.get('Target','')
                member=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
                if not member.startswith('xl/worksheets/'):
                    raise ValueError('工作表路径不正确')
                root=xml(member)
                if len(root.findall('x:sheetData/x:row',NS))>10007:
                    raise ValueError('单工作表不能超过10000条数据行')
                cells,formula_cells={},set()
                for node in root.findall('x:sheetData/x:row/x:c',NS):
                    address=node.get('r','')
                    if not re.fullmatch(r'[A-Z]{1,3}[1-9]\d{0,5}',address):
                        raise ValueError('单元格地址无效')
                    if int(re.search(r'\d+',address)[0])>10020 or column_number(address.rstrip('0123456789'))>256:
                        raise ValueError('工作表超出支持的10000行报价／256列范围')
                    if node.find('x:f',NS) is not None:
                        formula_cells.add(address)
                    value=node.find('x:v',NS)
                    value=value.text or '' if value is not None else ''
                    kind=node.get('t')
                    if kind=='s':
                        value=shared[int(value)]
                    elif kind=='inlineStr':
                        value=''.join(node.find('x:is',NS).itertext())
                    elif kind=='d':
                        value=value[:10]
                    elif kind=='b':
                        value='布尔值：'+value
                    elif kind=='e':
                        value='Excel错误：'+value
                    elif value and int(node.get('s','0')) in date_styles and kind not in ('str','e','b'):
                        serial=Decimal(value)
                        if serial!=serial.to_integral_value():
                            value='含时分的日期：'+value
                        elif not is1904 and serial==60:
                            value='无效日期1900-02-29'
                        else:
                            epoch=date(1904,1,1) if is1904 else date(1899,12,31) if serial<60 else date(1899,12,30)
                            value=(epoch+timedelta(days=int(serial))).isoformat()
                    elif value and kind in (None,'n'):
                        number=Decimal(value)
                        if not number.is_finite() or len(value)>64 or number.adjusted()>20 or number.adjusted() < -20:
                            raise ValueError('数值超出支持范围')
                        value=format(number,'f')
                    if len(value)>32767:
                        raise ValueError('单元格文字过长')
                    cells[address]=value
                merges=[]
                merged_cells=0
                for node in root.findall('x:mergeCells/x:mergeCell',NS):
                    match=re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)',node.get('ref',''))
                    if not match:
                        raise ValueError('合并单元格范围无效')
                    c1,r1,c2,r2=match.groups()
                    if int(r2)-int(r1)>10000 or column_number(c2)-column_number(c1)>100:
                        raise ValueError('合并单元格范围过大')
                    merged_cells+=(int(r2)-int(r1)+1)*(column_number(c2)-column_number(c1)+1)
                    if merged_cells>50000:raise ValueError('合并单元格总量过大')
                    merges.append((c1,int(r1),c2,int(r2)))
                result.append({'name':sheet.get('name',''),'cells':cells,'formulas':formula_cells,'merges':merges})
            if not result or len(result)>50:
                raise ValueError('工作表数量须为1～50')
            return result
        except (KeyError,IndexError,E.ParseError,OverflowError,ArithmeticError,BadZipFile,RuntimeError,NotImplementedError) as exc:
            raise ValueError('Excel内容损坏或结构不支持，请重新另存为xlsx') from exc


def safe_text(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]','',str(value))


def xml_bytes(root):
    return E.tostring(root,encoding='utf-8',xml_declaration=True)


def write_workbook(sheets):
    """sheets: [{name, metadata:[str], headers:[str], rows:[[typed values]]}]."""
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as z:
        content=E.Element('Types',xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
        for ext,kind in [('rels','application/vnd.openxmlformats-package.relationships+xml'),('xml','application/xml')]:
            E.SubElement(content,'Default',Extension=ext,ContentType=kind)
        for path,kind in [('/xl/workbook.xml','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'),('/xl/styles.xml','application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml')]:
            E.SubElement(content,'Override',PartName=path,ContentType=kind)
        package=E.Element('Relationships',xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
        E.SubElement(package,'Relationship',Id='rId1',Type=REL+'/officeDocument',Target='xl/workbook.xml')
        z.writestr('_rels/.rels',xml_bytes(package))
        workbook=E.Element('workbook',xmlns=MAIN,attrib={'xmlns:r':REL})
        E.SubElement(workbook,'workbookPr',date1904='0')
        sheet_nodes=E.SubElement(workbook,'sheets')
        relations=E.Element('Relationships',xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
        E.SubElement(relations,'Relationship',Id='styles',Type=REL+'/styles',Target='styles.xml')
        used_names=set()
        for index,sheet in enumerate(sheets,1):
            name=re.sub(r'[\\/*?:\[\]]','_',safe_text(sheet['name'])).strip(" '")[:31] or '报价'
            base=name
            suffix=1
            while name.casefold() in used_names:
                suffix+=1;name=base[:27]+'_'+str(suffix)
            used_names.add(name.casefold())
            E.SubElement(sheet_nodes,'sheet',name=name,sheetId=str(index),attrib={'r:id':f'sheet{index}'})
            E.SubElement(relations,'Relationship',Id=f'sheet{index}',Type=REL+'/worksheet',Target=f'worksheets/sheet{index}.xml')
            E.SubElement(content,'Override',PartName=f'/xl/worksheets/sheet{index}.xml',ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
            root=E.Element('worksheet',xmlns=MAIN)
            views=E.SubElement(root,'sheetViews');view=E.SubElement(views,'sheetView',workbookViewId='0',showGridLines='0')
            frozen_columns=sheet['headers'].index('中文港口名')+1 if '中文港口名' in sheet['headers'] else 2
            first_scroll_cell=f'{column(frozen_columns+1)}8'
            E.SubElement(view,'pane',xSplit=str(frozen_columns),ySplit='7',topLeftCell=first_scroll_cell,activePane='bottomRight',state='frozen')
            E.SubElement(view,'selection',pane='bottomRight',activeCell=first_scroll_cell,sqref=first_scroll_cell)
            E.SubElement(root,'sheetFormatPr',defaultRowHeight='22')
            columns=E.SubElement(root,'cols')
            for col,title in enumerate(sheet['headers'],1):
                width=16 if title in ('20GP','40GP','40HQ','货币','有效期起','有效期止') else 32 if title in ('备注（注意事项）','途径路径','价格来源','适用条件','已含费用','另计费用','待确认费用','报价编号') else 22
                E.SubElement(columns,'col',min=str(col),max=str(col),width=str(width),customWidth='1')
            data=E.SubElement(root,'sheetData')
            covered=set()
            for ref in sheet.get('merge_ranges',[]):
                match=re.fullmatch(r'([B-E])(\d+):\1(\d+)',ref)
                if not match:raise ValueError('港口合并范围无效')
                col,start,end=match.groups()
                covered.update(f'{col}{r}' for r in range(int(start)+1,int(end)+1))
            def add_row(number,values,style=0):
                long=max((len(str(v)) for v in values if v is not None),default=0)
                height=30 if number==1 else 36 if number==7 else min(150,max(30,((long+20)//22)*15)) if number>=8 else 26
                row=E.SubElement(data,'row',r=str(number),ht=str(height),customHeight='1')
                for col,value in enumerate(values,1):
                    if value is None or f'{column(col)}{number}' in covered:continue
                    cell=E.SubElement(row,'c',r=f'{column(col)}{number}',s=str(style))
                    if isinstance(value,(date,datetime)):
                        cell.set('s','4')
                        day=value.date() if isinstance(value,datetime) else value
                        E.SubElement(cell,'v').text=str((day-date(1899,12,30)).days)
                    elif isinstance(value,(Decimal,int)) and not isinstance(value,bool):
                        cell.set('s','3' if isinstance(value,Decimal) else str(style))
                        E.SubElement(cell,'v').text=str(value)
                    else:
                        cell.set('t','inlineStr')
                        inline=E.SubElement(cell,'is');E.SubElement(inline,'t',attrib={'xml:space':'preserve'}).text=safe_text(value)
            add_row(1,[sheet.get('title',sheet['name']+'报价表')],1)
            for rownum,text in enumerate(sheet['metadata'][:4],2):add_row(rownum,[text],5)
            add_row(7,sheet['headers'],2)
            for rownum,values in enumerate(sheet['rows'],8):add_row(rownum,values,6 if rownum%2 else 0)
            E.SubElement(root,'autoFilter',ref=f'A7:{column(len(sheet["headers"]))}{7+len(sheet["rows"])}')
            merges=E.SubElement(root,'mergeCells',count='5')
            # Metadata stays within the first 18 template columns for a readable opening view.
            last=column(min(18,len(sheet['headers'])))
            for rownum in range(1,6):E.SubElement(merges,'mergeCell',ref=f'A{rownum}:{last}{rownum}')
            for ref in sheet.get('merge_ranges',[]):E.SubElement(merges,'mergeCell',ref=ref)
            merges.set('count',str(len(merges)))
            E.SubElement(root,'pageMargins',left='0.25',right='0.25',top='0.4',bottom='0.4',header='0.2',footer='0.2')
            E.SubElement(root,'pageSetup',orientation='landscape',paperSize='9',fitToWidth='1',fitToHeight='0')
            z.writestr(f'xl/worksheets/sheet{index}.xml',xml_bytes(root))
        z.writestr('xl/workbook.xml',xml_bytes(workbook));z.writestr('xl/_rels/workbook.xml.rels',xml_bytes(relations))
        z.writestr('[Content_Types].xml',xml_bytes(content))
        z.writestr('xl/styles.xml',STYLES)
    return output.getvalue()


STYLES='''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="yyyy-mm-dd"/></numFmts><fonts count="4"><font><sz val="10"/><color rgb="FF19354B"/><name val="Arial"/></font><font><b/><sz val="14"/><color rgb="FF19354B"/><name val="Arial"/></font><font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/></font><font><i/><sz val="10"/><color rgb="FF607B8C"/><name val="Arial"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF193B54"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF0F6FA"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="7"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment horizontal="right" vertical="center"/></xf><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
