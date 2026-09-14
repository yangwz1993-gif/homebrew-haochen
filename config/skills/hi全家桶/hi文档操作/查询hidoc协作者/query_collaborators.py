#!/usr/bin/env python3
"""
查询 REDoc 文档 / 独立表格 / 多维表格的协作者名单。

用法:
    python3 query_collaborators.py <文档链接或 shortcutId> [--json] [--sso /path/to/sso.json]

说明:
    - spaceId 会自动通过 queryShortcutPath 解析，无需手动传入
    - 同时适用于 /doc/、/sheet/、/table/ 三种链接
    - 依赖 /home/node/sso.json 中的 cookieHeader 提供登录态

输出:
    协作者清单（个人 / 群组 / 部门 + 角色）、可见性级别、链接分享开关、权限继承来源
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

BASE = "https://docs.xiaohongshu.com"
DEFAULT_SSO = "/home/node/sso.json"

# memberType 语义
MEMBER_TYPE = {
    1: "个人",
    2: "群组",
    3: "部门",
}

# visibilityStatus 语义（对应 REDoc 权限面板「谁可找到并访问文档」）
VISIBILITY = {
    0: "L3-私密 · 仅文档协作者可访问、可搜到",
    1: "L2-内部公开 · 全员可访问、可搜到",
    2: "L1-公开 · 可对外分享",
}


def load_cookie(sso_path):
    if not os.path.exists(sso_path):
        sys.exit(f"[错误] 找不到登录态文件: {sso_path}")
    with open(sso_path, encoding="utf-8") as f:
        sso = json.load(f)
    cookie = sso.get("cookieHeader")
    if not cookie:
        sys.exit(f"[错误] {sso_path} 中没有 cookieHeader 字段")
    return cookie


def extract_shortcut_id(raw):
    """从 /doc/xxx、/sheet/xxx、/table/xxx 链接或裸 id 中提取 shortcutId"""
    raw = raw.strip()
    m = re.search(r"/(?:doc|sheet|table)/([0-9a-fA-F]{32})", raw)
    if m:
        return m.group(1)
    m = re.fullmatch(r"[0-9a-fA-F]{32}", raw)
    if m:
        return raw
    sys.exit(f"[错误] 无法从输入中解析 shortcutId: {raw}")


def get_json(url, cookie):
    req = urllib.request.Request(url, headers={
        "Cookie": cookie,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        sys.exit(f"[错误] 接口未返回 JSON（通常是登录态失效）: {url}\n{body[:200]}")


def resolve_space_id(shortcut_id, cookie):
    """通过文档路径链路解析所属 spaceId"""
    url = f"{BASE}/docgateway/api/menu/queryShortcutPath/{shortcut_id}"
    data = get_json(url, cookie).get("data") or []
    for node in data:
        if node.get("type") == "SPACE" and node.get("spaceId"):
            return node["spaceId"], node.get("title")
    sys.exit("[错误] 未能解析出 spaceId，请确认对该文档有访问权限")


def query_collaborators(shortcut_id, space_id, cookie):
    qs = urllib.parse.urlencode({"shortcutId": shortcut_id, "spaceId": space_id})
    url = f"{BASE}/docgateway/api/shortcutMember/queryExclusiveCollaboratorInfo?{qs}"
    resp = get_json(url, cookie)
    if not resp.get("success"):
        sys.exit(f"[错误] 协作者接口返回失败: {resp.get('errorMsg') or resp.get('alertMsg')}")
    return resp.get("data") or {}


def query_meta(shortcut_id, cookie):
    """取标题、所有者等基础元信息（可选，失败不阻断）"""
    url = f"{BASE}/docgateway/api/menu/queryShortcutWithMenuTree/{shortcut_id}"
    try:
        return get_json(url, cookie).get("data") or {}
    except Exception:
        return {}


def normalize(data):
    """把接口原始结构整理成扁平结果"""
    items = []
    for m in data.get("exclusiveItemList") or []:
        role = m.get("role") or {}
        items.append({
            "name": m.get("memberName"),
            "type": MEMBER_TYPE.get(m.get("memberType"), f"未知({m.get('memberType')})"),
            "department": m.get("department"),
            "role": role.get("roleName"),
            "roleCode": role.get("roleCode"),
            "memberId": m.get("memberId"),
            "joinTime": m.get("joinSpaceTime"),
        })

    inherited = data.get("inheritedObjectVo") or {}
    obj = inherited.get("objectVo") or {}
    role = inherited.get("roleVo") or {}

    return {
        "shortcutId": data.get("shortcutId"),
        "spaceId": data.get("spaceId"),
        "visibilityStatus": data.get("visibilityStatus"),
        "visibilityText": VISIBILITY.get(data.get("visibilityStatus"), "未知"),
        "shareLinkOn": data.get("shareLinkSwitchStatus") == 1,
        "collaborators": items,
        "inheritedFrom": {
            "name": obj.get("objectName"),
            "shortcutId": obj.get("objectId"),
            "role": role.get("roleName"),
        } if obj.get("objectName") else None,
    }


def print_report(result, title=None, space_title=None):
    print("=" * 60)
    print(f"文档: {title or result['shortcutId']}")
    if space_title:
        print(f"空间: {space_title}")
    print(f"安全级别: {result['visibilityText']}")
    print(f"链接免申请访问: {'已开启（拿到链接即可成为可阅读协作者）' if result['shareLinkOn'] else '已关闭'}")
    print("=" * 60)

    items = result["collaborators"]
    persons = [i for i in items if i["type"] == "个人"]
    groups = [i for i in items if i["type"] != "个人"]

    print(f"\n协作者条目共 {len(items)} 条（个人 {len(persons)} · 群组/部门 {len(groups)}）\n")
    for i in items:
        dept = f" · {i['department']}" if i.get("department") else ""
        print(f"  [{i['type']}] {i['name']}{dept}  →  {i['role']}")

    if groups:
        print("\n  ⚠ 存在群组/部门授权，实际可访问人数取决于群成员规模，需单独核查")

    if result["inheritedFrom"]:
        inh = result["inheritedFrom"]
        print(f"\n权限继承自上级节点: {inh['name']}（继承角色: {inh['role']}）")
    print()


def main():
    ap = argparse.ArgumentParser(description="查询 REDoc 文档协作者")
    ap.add_argument("target", help="文档链接或 32 位 shortcutId")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出，便于二次处理")
    ap.add_argument("--sso", default=DEFAULT_SSO, help=f"登录态文件路径，默认 {DEFAULT_SSO}")
    args = ap.parse_args()

    cookie = load_cookie(args.sso)
    shortcut_id = extract_shortcut_id(args.target)
    space_id, space_title = resolve_space_id(shortcut_id, cookie)
    data = query_collaborators(shortcut_id, space_id, cookie)
    result = normalize(data)

    meta = query_meta(shortcut_id, cookie)
    title = meta.get("title") if isinstance(meta, dict) else None

    if args.json:
        result["title"] = title
        result["spaceTitle"] = space_title
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result, title=title, space_title=space_title)


if __name__ == "__main__":
    main()
