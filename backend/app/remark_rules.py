"""Only explicit business remark labels authorize automatic remark extraction."""
import re
import unicodedata

LABEL = re.compile(r'备注|備註|注意事项|注意事項|特别说明|特別說明|\b(?:remarks?|notes?|special\s+instructions?)\b', re.I)

def labelled(text):
    return bool(LABEL.search(unicodedata.normalize('NFKC', str(text))))
