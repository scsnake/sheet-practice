"""所有 MusicXML 共用的 parser。

樂譜檔多半是從網路下載的，內容不可信任：不展開實體、不讀 DTD 或外部檔案、不連網路。
huge_tree 留著，因為完整的管弦樂總譜會超過 libxml2 預設的大小限制；實體不展開，所以不會被「實體炸彈」拖垮。
"""

from __future__ import annotations

from lxml import etree

PARSER = etree.XMLParser(
    remove_blank_text=True,
    huge_tree=True,
    resolve_entities=False,
    load_dtd=False,
    no_network=True,
)
