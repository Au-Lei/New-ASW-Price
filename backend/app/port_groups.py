"""Display-only port grouping; quotation rows and stored identities stay intact."""

def port_key(item):
    if not item.get('destination_port_id'):return None
    return (item.get('origin_port_id',''),item['destination_port_id'],item.get('route_id',''),
            item['route'].get('name',''),item['country'].get('name_en',''),item['country'].get('name_cn',''),
            item['destination'].get('name_en',''),item['destination'].get('name_cn',''))

def grouped(rows,keys):
    buckets={}
    for index,(row,key) in enumerate(zip(rows,keys)):
        buckets.setdefault(('port',key) if key is not None else ('unknown',index),[]).append((row,key))
    pairs=[pair for bucket in buckets.values() for pair in bucket]
    return [r for r,k in pairs],[k for r,k in pairs]

def merge_ranges(keys,first_row):
    ranges=[];start=0
    while start<len(keys):
        end=start+1
        while keys[start] is not None and end<len(keys) and keys[end]==keys[start]:end+=1
        if end-start>1:
            for col in 'BCDE':ranges.append(f'{col}{start+first_row}:{col}{end+first_row-1}')
        start=end
    return ranges
