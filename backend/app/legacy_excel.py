"""Read Excel 97-2003 into the same bounded sheet model as OOXML."""
from io import StringIO
import math
import struct
import xlrd
from xlrd.compdoc import CompDoc

OLE_MAGIC=b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'

def read_xls(content):
    from .excel_format import column
    try:
        ole=CompDoc(content,logfile=StringIO())
        stream=ole.get_named_stream('Workbook') or ole.get_named_stream('Book')
        if not stream:raise ValueError('文件不是Excel工作簿；若为加密xlsx，请另存为未加密xlsx')
        offsets=[];pos=0
        while pos+4<=len(stream):
            kind,size=struct.unpack_from('<HH',stream,pos);body=stream[pos+4:pos+4+size]
            if len(body)!=size:raise ValueError('xls记录损坏，请重新另存为xlsx')
            if kind==0x2f:raise ValueError('不支持加密Excel，请提供未加密文件')
            if kind==0x85 and size>=8 and body[5]==0:offsets.append(struct.unpack_from('<I',body)[0])
            pos+=4+size
        book=xlrd.open_workbook(file_contents=content,formatting_info=True,logfile=StringIO(),on_demand=True)
        try:
            if book.biff_version!=80:raise ValueError('仅支持Excel 97-2003版xls；更早版本请另存为xlsx')
            if not 1<=book.nsheets<=50:raise ValueError('工作表数量须为1～50')
            if len(offsets)!=book.nsheets:raise ValueError('xls工作表结构不支持，请另存为xlsx')
            result=[];total=0
            for index in range(book.nsheets):
                sheet=book.sheet_by_index(index)
                if sheet.nrows>10020 or sheet.ncols>256:raise ValueError('工作表超出支持的10000行报价／256列范围')
                total+=sheet.nrows*sheet.ncols
                if total>1000000:raise ValueError('Excel单元格总量超限')
                formulas=set();pos=offsets[index]
                while pos+4<=len(stream):
                    kind,size=struct.unpack_from('<HH',stream,pos)
                    if pos+4+size>len(stream):raise ValueError('xls记录损坏，请另存为xlsx')
                    if kind==0xa:break
                    if kind==0x6 and size>=6:
                        row,col=struct.unpack_from('<HH',stream,pos+4);formulas.add(f'{column(col+1)}{row+1}')
                    pos+=4+size
                cells={}
                for row in range(sheet.nrows):
                    for col in range(sheet.row_len(row)):
                        cell=sheet.cell(row,col);address=f'{column(col+1)}{row+1}'
                        if address in formulas:value=''
                        elif cell.ctype==xlrd.XL_CELL_DATE:value=xlrd.xldate_as_datetime(cell.value,book.datemode).date().isoformat()
                        elif cell.ctype==xlrd.XL_CELL_NUMBER:
                            if not math.isfinite(cell.value) or abs(cell.value)>1e20:raise ValueError('数值超出支持范围')
                            value=format(cell.value,'.15g')
                        elif cell.ctype==xlrd.XL_CELL_BOOLEAN:value='TRUE' if cell.value else 'FALSE'
                        elif cell.ctype==xlrd.XL_CELL_ERROR:value=''
                        else:value=str(cell.value or '')
                        if len(value)>32767:raise ValueError('单元格文字过长')
                        if value or address in formulas:cells[address]=value
                merges=[];merged=0
                for top,bottom,left,right in sheet.merged_cells:
                    merged+=(bottom-top)*(right-left)
                    if bottom-top>10001 or right-left>101 or merged>50000:raise ValueError('合并单元格范围或总量过大')
                    merges.append((column(left+1),top+1,column(right),bottom))
                result.append({'name':sheet.name,'cells':cells,'formulas':formulas,'merges':merges})
                book.unload_sheet(index)
            return result
        finally:book.release_resources()
    except ValueError:raise
    except Exception as exc:
        raise ValueError('xls文件损坏、加密或结构不支持，请另存为未加密xlsx后上传') from exc
