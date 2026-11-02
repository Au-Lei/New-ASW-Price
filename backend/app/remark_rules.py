"""Only explicit business remark labels authorize automatic remark extraction."""
import re
import unicodedata

LABEL = re.compile(r'备注|備註|注意事项|注意事項|\b(?:remarks?|notes?)\b', re.I)

def labelled(text):
    return bool(LABEL.search(unicodedata.normalize('NFKC', str(text))))
