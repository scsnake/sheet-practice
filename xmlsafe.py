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

# 一個 MusicXML 檔解壓後的上限。完整的管弦樂總譜約 10–40 MB；壓縮檔可以小檔案解出巨量資料（zip 炸彈），所以一定要設上限。
MAX_XML_BYTES = 200 * 1024 * 1024


def read_limited(stream, limit: int = MAX_XML_BYTES, name: str = "") -> bytes:
    """最多讀 limit 個位元組；超過就報錯（不相信 zip 標頭裡寫的大小）。"""
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{name or '檔案'} 解壓後超過 {limit // (1024 * 1024)} MB")
    return data


def read_mxl(path) -> bytes:
    """讀 .mxl（壓縮的 MusicXML）裡的主要樂譜檔，有大小上限。"""
    import zipfile

    with zipfile.ZipFile(path) as z:
        with z.open("META-INF/container.xml") as f:
            container = etree.fromstring(read_limited(f, 1024 * 1024, "container.xml"), PARSER)
        rootfile = container.find(".//rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError(f"{getattr(path, 'name', path)}：container.xml 沒有指出樂譜檔")
        with z.open(rootfile.get("full-path")) as f:
            return read_limited(f, MAX_XML_BYTES, rootfile.get("full-path"))
