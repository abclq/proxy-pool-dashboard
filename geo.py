# -*- coding: utf-8 -*-
"""GeoIP resolver — ip2region xdb, city-level for China, country for foreign."""
import os, searcher, util

import threading

XDB_PATH = os.environ.get("IP2REGION_DB", "/app/ip2region.xdb")
_searcher = None
_init_lock = threading.Lock()

PROVINCE_SHORT = {
    "北京": "北京", "北京市": "北京", "上海": "上海", "上海市": "上海",
    "天津": "天津", "天津市": "天津", "重庆": "重庆", "重庆市": "重庆",
    "广东": "广东", "广东省": "广东", "浙江": "浙江", "浙江省": "浙江",
    "江苏": "江苏", "江苏省": "江苏", "四川": "四川", "四川省": "四川",
    "湖北": "湖北", "湖北省": "湖北", "山东": "山东", "山东省": "山东",
    "福建": "福建", "福建省": "福建", "河南": "河南", "河南省": "河南",
    "湖南": "湖南", "湖南省": "湖南", "河北": "河北", "河北省": "河北",
    "安徽": "安徽", "安徽省": "安徽", "辽宁": "辽宁", "辽宁省": "辽宁",
    "陕西": "陕西", "陕西省": "陕西", "江西": "江西", "江西省": "江西",
    "广西": "广西", "广西壮族自治区": "广西",
    "云南": "云南", "云南省": "云南", "贵州": "贵州", "贵州省": "贵州",
    "山西": "山西", "山西省": "山西", "吉林": "吉林", "吉林省": "吉林",
    "黑龙江": "黑龙江", "黑龙江省": "黑龙江", "甘肃": "甘肃", "甘肃省": "甘肃",
    "海南": "海南", "海南省": "海南", "内蒙古": "内蒙古", "内蒙古自治区": "内蒙古",
    "新疆": "新疆", "新疆维吾尔自治区": "新疆", "西藏": "西藏", "西藏自治区": "西藏",
    "宁夏": "宁夏", "宁夏回族自治区": "宁夏", "青海": "青海", "青海省": "青海",
}
MUNICIPALITY = {"北京", "上海", "天津", "重庆", "香港", "澳门"}
COUNTRY_CODE = {
    "CN": "中国", "US": "美国", "JP": "日本", "KR": "韩国", "SG": "新加坡",
    "DE": "德国", "GB": "英国", "FR": "法国", "CA": "加拿大", "AU": "澳大利亚",
    "NL": "荷兰", "IN": "印度", "BR": "巴西", "RU": "俄罗斯", "VN": "越南",
    "TH": "泰国", "MY": "马来西亚", "ID": "印尼", "PH": "菲律宾",
    "TW": "台湾", "HK": "香港", "MO": "澳门", "SE": "瑞典", "CH": "瑞士",
    "IT": "意大利", "ES": "西班牙", "PL": "波兰", "TR": "土耳其", "MX": "墨西哥",
    "AR": "阿根廷", "AE": "阿联酋", "UA": "乌克兰", "CZ": "捷克", "AT": "奥地利",
    "BE": "比利时", "DK": "丹麦", "FI": "芬兰", "NO": "挪威", "IE": "爱尔兰",
    "PT": "葡萄牙", "IL": "以色列", "NZ": "新西兰", "GR": "希腊", "RO": "罗马尼亚",
    "BG": "保加利亚", "HU": "匈牙利", "EG": "埃及", "SA": "沙特", "CO": "哥伦比亚",
    "PK": "巴基斯坦", "BD": "孟加拉国", "KZ": "哈萨克斯坦", "PE": "秘鲁",
    "ZA": "南非", "CL": "智利", "NG": "尼日利亚", "KE": "肯尼亚", "MA": "摩洛哥",
    "DZ": "阿尔及利亚",
}
# Reverse map: English country name → CN code
_EN_TO_CODE = {
    "united states": "US", "australia": "AU", "japan": "JP", "south korea": "KR",
    "singapore": "SG", "germany": "DE", "united kingdom": "GB", "france": "FR",
    "canada": "CA", "netherlands": "NL", "india": "IN", "brazil": "BR",
    "russia": "RU", "vietnam": "VN", "thailand": "TH", "malaysia": "MY",
    "indonesia": "ID", "philippines": "PH", "sweden": "SE", "switzerland": "CH",
    "italy": "IT", "spain": "ES", "poland": "PL", "turkey": "TR", "mexico": "MX",
    "argentina": "AR", "ukraine": "UA", "czech": "CZ", "austria": "AT",
    "belgium": "BE", "denmark": "DK", "finland": "FI", "norway": "NO",
    "ireland": "IE", "portugal": "PT", "israel": "IL", "greece": "GR",
    "romania": "RO", "bulgaria": "BG", "hungary": "HU", "egypt": "EG",
    "saudi arabia": "SA", "colombia": "CO", "pakistan": "PK",
    "bangladesh": "BD", "kazakhstan": "KZ", "peru": "PE", "south africa": "ZA",
    "chile": "CL", "nigeria": "NG", "kenya": "KE", "morocco": "MA",
    "algeria": "DZ", "new zealand": "NZ",
}

def _init():
    global _searcher
    if _searcher is not None:
        return
    with _init_lock:
        if _searcher is not None:  # 双重检查
            return
        if not os.path.exists(XDB_PATH):
            return
        _searcher = searcher.new_with_file_only(util.IPv4, XDB_PATH)

def resolve(ip):
    """ip → '浙江 杭州' (China city) or '美国' (foreign country) or '?'"""
    _init()
    if _searcher is None or not ip:
        return "?"
    try:
        result = _searcher.search(ip)
        if not result:
            return "?"
        parts = result.split("|")
        if len(parts) < 5:
            return "?"
        # ip2region 格式: 国家|区域|省份|城市|ISP
        country, region, province, city, isp = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[4].strip()

        # Is it China?
        is_china = country == "中国"
        if is_china:
            provinceshort = PROVINCE_SHORT.get(province)
            if provinceshort:
                prov = provinceshort
            elif province and province != "0":
                prov = province
            else:
                prov = ""

            # If province is missing (ip2region returns "0"), show "中国"
            if not prov:
                c = city
                if c == "0":
                    c = ""
                if not c:
                    return "中国"
                return c
            # Normalize 香港/澳门 special regions
            if prov in ("香港特别行政区",):
                prov = "香港"
            if prov == "台湾省" or prov == "台湾":
                prov = "台湾"
                return prov
            if prov in MUNICIPALITY:
                return prov
            # Strip administrative suffixes but keep 州 (it's part of city names: 杭州, 苏州, etc.)
            # Only strip 州 if preceded by 自治 (自治州 suffix)
            c = city
            if c.endswith("自治州"):
                c = c[:-3]
            c = c.rstrip("区县市")
            if not c or c == "0":
                return prov
            return f"{prov} {c}"
        else:
            # Foreign — resolve country name to Chinese
            # Try province (parts[2]) first for HK/TW/MO as fallback
            if province in ("香港特别行政区", "香港"):
                return "香港"
            if province in ("台湾省", "台湾"):
                return "台湾"
            if province in ("澳门特别行政区", "澳门"):
                return "澳门"
            # Try country name mapping
            code = _EN_TO_CODE.get(country.lower())
            if code:
                return COUNTRY_CODE.get(code, country)
            return country if country and country != "0" else "?"
    except Exception:
        return "?"

def resolve_region(ip):
    """ip → 'CN' / 'US' / 'JP' etc"""
    _init()
    if _searcher is None or not ip:
        return "?"
    try:
        result = _searcher.search(ip)
        if not result:
            return "?"
        parts = result.split("|")
        if len(parts) < 5:
            return "?"
        # ip2region 格式: 国家|区域|省份|城市|ISP
        country = parts[0].strip()
        province = parts[2].strip()
        # HK/TW/MO are under CN in ip2region — override
        if province in ("香港特别行政区", "香港"):
            return "HK"
        if province in ("台湾省", "台湾"):
            return "TW"
        if province in ("澳门特别行政区", "澳门"):
            return "MO"
        # China
        if country == "中国":
            return "CN"
        # Foreign — try English name → code
        code = _EN_TO_CODE.get(country.lower())
        return code if code else "?"
    except Exception:
        return "?"

if __name__ == "__main__":
    tests = ["8.8.8.8", "1.1.1.1", "223.5.5.5", "120.26.123.95", 
             "121.43.102.172", "47.96.42.30", "112.28.149.156", 
             "47.121.182.36", "77.88.55.80", "103.235.46.39"]
    for ip in tests:
        print(f"{ip:20s} → {resolve(ip):12s}  [{resolve_region(ip)}]")
