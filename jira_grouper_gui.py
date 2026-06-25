#!/usr/bin/env python3
"""
Jira JQL 그루퍼 — tkinter GUI
담당자별 그룹핑 후 Slack DM 전송 / 복사
"""

import json
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from base64 import b64encode
from pathlib import Path
from tkinter import messagebox, ttk


def get_base_dir() -> Path:
    """exe(frozen) 환경과 py 환경 모두에서 실행 파일 기준 폴더 반환"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


CONFIG_FILE = get_base_dir() / "jira_grouper_config.json"
DEFAULT_CONFIG = {
    "domain": "kongstudios.atlassian.net",
    "email": "",
    "api_token": "",
    "last_jql": "",
    "fmt": "slack",
    "slack_mentions": {},
    "slack_bot_token": "",
    "slack_my_uid": "",
    "jql_history": [],
    "issue_prefix": "",
}

BG   = "#1e1e2e"
BG2  = "#2a2a3d"
BG3  = "#12121f"
FG   = "#e0e0f0"
FG2  = "#9090aa"
ACC  = "#7c7cff"
ERR  = "#ff6b6b"
OK   = "#6bffb8"
LINE = "#3a3a55"


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        cfg = {**DEFAULT_CONFIG, **saved}
        if "slack_mentions" not in cfg:
            cfg["slack_mentions"] = {}
        return cfg
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def fetch_issues(domain, email, token, jql):
    import urllib.request, urllib.error
    b64 = b64encode(f"{email}:{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {b64}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    url = f"https://{domain}/rest/api/3/search/jql"
    issues, next_page_token = [], None
    while True:
        body = {"jql": jql, "maxResults": 100,
                "fields": ["summary", "assignee", "status", "priority"]}
        if next_page_token:
            body["nextPageToken"] = next_page_token
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise Exception(f"HTTP {e.code} {e.reason}\n\n{err_body}")
        batch = data.get("issues", [])
        issues.extend(batch)
        next_page_token = data.get("nextPageToken")
        if not batch or not next_page_token:
            break
    return issues


def group_and_format(issues, domain, fmt, mentions, grouping=True):
    base = f"https://{domain}/browse/"
    total = len(issues)

    if not grouping:
        # 그룹핑 OFF: 이슈 순서 그대로 flat 출력
        if fmt == "plain":
            lines = [f"* {i['key']} ({base}{i['key']}): {i['fields']['summary']}" for i in issues]
        else:
            lines = [f"• <{base}{i['key']}|{i['key']}>: {i['fields']['summary']}" for i in issues]
        return "\n".join(lines).strip(), total, 0, []

    groups = {}
    for iss in issues:
        a = (iss["fields"].get("assignee") or {}).get("displayName", "미배정")
        groups.setdefault(a, []).append(iss)
    groups = dict(sorted(groups.items()))

    if fmt == "plain":
        lines = []
        for name, lst in groups.items():
            lines.append(f"* {name}")
            for i in lst:
                key = i["key"]
                lines.append(f"   * {key} ({base}{key}): {i['fields']['summary']}")
            lines.append("")
        return "\n".join(lines).strip(), total, len(groups), list(groups.keys())

    preview_lines = []
    for name, lst in groups.items():
        uid = mentions.get(name, "")
        header = f"*{name}*" + (f" <@{uid}>" if uid else "")
        preview_lines.append(header)
        for i in lst:
            key = i["key"]
            preview_lines.append(f"  • <{base}{key}|{key}>: {i['fields']['summary']}")
        preview_lines.append("")
    preview = "\n".join(preview_lines).strip()
    return preview, total, len(groups), list(groups.keys())


def build_slack_blocks(issues, domain, mentions, grouping=True):
    """담당자별(또는 flat) blocks 리스트 반환 — 스레드 분할 전송용"""
    base = f"https://{domain}/browse/"

    def _issue_elements(lst):
        elems = []
        for i in lst:
            key = i["key"]
            summary = i["fields"]["summary"]
            elems.append({
                "type": "rich_text_list",
                "style": "bullet",
                "indent": 0,
                "elements": [{"type": "rich_text_section", "elements": [
                    {"type": "link", "url": f"{base}{key}", "text": key},
                    {"type": "text", "text": f": {summary}"},
                ]}]
            })
        return elems

    if not grouping:
        # 그룹핑 OFF: 50개씩 잘라서 블록 분할
        chunks = [issues[i:i+50] for i in range(0, len(issues), 50)]
        return [[{
            "type": "rich_text",
            "elements": _issue_elements(chunk),
        }] for chunk in chunks]

    groups = {}
    for iss in issues:
        a = (iss["fields"].get("assignee") or {}).get("displayName", "미배정")
        groups.setdefault(a, []).append(iss)
    groups = dict(sorted(groups.items()))
    assignee_blocks = []

    for name, lst in groups.items():
        uid = mentions.get(name, "")
        if uid:
            header_elements = [{"type": "user", "user_id": uid}]
        else:
            header_elements = [{"type": "text", "text": name, "style": {"bold": True}}]

        assignee_blocks.append([{
            "type": "rich_text",
            "elements": [
                {"type": "rich_text_section", "elements": header_elements},
                *_issue_elements(lst),
            ]
        }])

    return assignee_blocks


def _post_message(bot_token, channel, payload):
    """Slack chat.postMessage 단일 호출"""
    import urllib.request, urllib.error
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"Slack HTTP {e.code}: {err_body}")
    if not resp.get("ok"):
        raise Exception(f"Slack 오류: {resp.get('error', 'unknown')}")
    return resp


def send_slack_dm(bot_token, user_id, assignee_blocks, total_issues, total_assignees, jql, grouping=True):
    """메인 메시지(요약) 전송 후 담당자별로 스레드에 분할 전송"""
    # 건수 텍스트: 그룹핑 ON/OFF에 따라 다르게
    if grouping:
        count_text = f"총 {total_issues}건 / 담당자 {total_assignees}명"
    else:
        count_text = f"총 {total_issues}건"

    summary_blocks = [
        {
            "type": "rich_text",
            "elements": [{
                "type": "rich_text_section",
                "elements": [
                    {"type": "text", "text": "Jira 이슈 조회 결과", "style": {"bold": True}},
                ]
            }]
        },
        {
            "type": "rich_text",
            "elements": [{
                "type": "rich_text_list",
                "style": "bullet",
                "elements": [
                    {"type": "rich_text_section", "elements": [
                        {"type": "text", "text": count_text}
                    ]},
                    {"type": "rich_text_section", "elements": [
                        {"type": "text", "text": "사용 JQL — ", "style": {"bold": True}},
                        {"type": "text", "text": jql, "style": {"code": True}},
                    ]},
                ]
            }]
        }
    ]
    resp = _post_message(bot_token, user_id, {
        "channel": user_id,
        "text": f"Jira 이슈 조회 결과 — {count_text}",
        "blocks": summary_blocks,
    })
    thread_ts = resp["ts"]

    # 담당자별 스레드 전송
    for blocks in assignee_blocks:
        _post_message(bot_token, user_id, {
            "channel": user_id,
            "text": "이슈 목록",
            "blocks": blocks,
            "thread_ts": thread_ts,
        })


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.title("Jira JQL 그루퍼")
        self.configure(bg=BG)
        self.minsize(720, 660)

        self.f     = tkfont.Font(family="맑은 고딕", size=10)
        self.fb    = tkfont.Font(family="맑은 고딕", size=10, weight="bold")
        self.fmono = tkfont.Font(family="Consolas", size=10)
        self.fh    = tkfont.Font(family="맑은 고딕", size=13, weight="bold")
        self.fs    = tkfont.Font(family="맑은 고딕", size=9, weight="bold")

        self._build_ui()

    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG2, pady=8, padx=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Jira JQL 그루퍼", font=self.fh, bg=BG2, fg=ACC).pack(side="left")
        self.status_lbl = tk.Label(hdr, text="", font=self.f, bg=BG2, fg=FG2)
        self.status_lbl.pack(side="right")

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("Dark.TNotebook", background=BG, borderwidth=0, tabmargins=0)
        style.configure("Dark.TNotebook.Tab",
                        background=BG2, foreground=FG2,
                        font=("맑은 고딕", 10), padding=[14, 6], borderwidth=0)
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", BG)],
                  foreground=[("selected", FG)])

        nb = ttk.Notebook(self, style="Dark.TNotebook")
        nb.pack(fill="both", expand=True)

        tab1 = tk.Frame(nb, bg=BG)
        nb.add(tab1, text="  조회  ")
        self._build_query_tab(tab1)

        tab2 = tk.Frame(nb, bg=BG)
        nb.add(tab2, text="  멘션 설정  ")
        self._build_mention_tab(tab2)

        tab3 = tk.Frame(nb, bg=BG)
        nb.add(tab3, text="  텍스트→JQL  ")
        self._build_text2jql_tab(tab3)

        tab4 = tk.Frame(nb, bg=BG)
        nb.add(tab4, text="  설정  ")
        self._build_settings_tab(tab4)

    # ── 조회 탭 ───────────────────────────────────────────────────────────────

    def _build_query_tab(self, parent):
        body = tk.Frame(parent, bg=BG, padx=14, pady=12)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="인증 정보", font=self.fs, bg=BG, fg=FG2).pack(anchor="w", pady=(0, 4))

        row0 = tk.Frame(body, bg=BG)
        row0.pack(fill="x", pady=(0, 6))
        tk.Label(row0, text="도메인", font=self.f, bg=BG, fg=FG2, width=8, anchor="w").pack(side="left")
        self.domain_var = tk.StringVar(value=self.cfg["domain"])
        self._entry(row0, self.domain_var, self.fmono).pack(side="left", fill="x", expand=True)

        row1 = tk.Frame(body, bg=BG)
        row1.pack(fill="x", pady=(0, 6))
        tk.Label(row1, text="이메일", font=self.f, bg=BG, fg=FG2, width=8, anchor="w").pack(side="left")
        self.email_var = tk.StringVar(value=self.cfg["email"])
        self._entry(row1, self.email_var, self.fmono).pack(side="left", fill="x", expand=True, padx=(0, 12))
        tk.Label(row1, text="API 토큰", font=self.f, bg=BG, fg=FG2).pack(side="left")
        self.token_var = tk.StringVar(value=self.cfg["api_token"])
        self._entry(row1, self.token_var, self.fmono, show="•").pack(side="left", fill="x", expand=True, padx=(6, 0))

        save_row = tk.Frame(body, bg=BG)
        save_row.pack(fill="x", pady=(0, 2))
        self._btn(save_row, "저장", self._save_auth, self.f).pack(side="right")

        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=8)

        jql_hdr = tk.Frame(body, bg=BG)
        jql_hdr.pack(fill="x", pady=(0, 4))
        tk.Label(jql_hdr, text="JQL", font=self.fs, bg=BG, fg=FG2).pack(side="left")
        self.history_btn = self._btn(jql_hdr, "▾ 최근 기록", self._show_history_menu, self.f)
        self.history_btn.pack(side="right")

        self.jql_text = tk.Text(body, height=3, font=self.fmono,
                                bg=BG3, fg=FG, insertbackground=FG,
                                relief="flat", bd=0, padx=8, pady=6, wrap="word",
                                highlightthickness=1, highlightbackground=LINE, highlightcolor=ACC)
        self.jql_text.pack(fill="x", pady=(0, 8))
        if self.cfg.get("last_jql"):
            self.jql_text.insert("1.0", self.cfg["last_jql"])
        self.jql_text.bind("<Control-Return>", lambda e: self._run())

        ctrl = tk.Frame(body, bg=BG)
        ctrl.pack(fill="x", pady=(0, 8))
        self.fmt_var = tk.StringVar(value=self.cfg.get("fmt", "slack"))
        for val, txt in [("slack", "Slack mrkdwn"), ("plain", "일반 텍스트")]:
            tk.Radiobutton(ctrl, text=txt, variable=self.fmt_var, value=val,
                           font=self.f, bg=BG, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=FG).pack(side="left", padx=(0, 12))

        tk.Frame(ctrl, bg=LINE, width=1).pack(side="left", fill="y", padx=10)

        self.grouping_var = tk.BooleanVar(value=True)
        tk.Checkbutton(ctrl, text="담당자별 그룹핑", variable=self.grouping_var,
                       font=self.f, bg=BG, fg=FG, selectcolor=BG2,
                       activebackground=BG, activeforeground=FG,
                       command=self._apply_filter).pack(side="left")

        self.run_btn = self._btn(ctrl, "▶  조회", self._run, self.fb, accent=True)
        self.run_btn.pack(side="right")

        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=(0, 8))

        res_hdr = tk.Frame(body, bg=BG)
        res_hdr.pack(fill="x", pady=(0, 4))
        tk.Label(res_hdr, text="결과", font=self.fs, bg=BG, fg=FG2).pack(side="left")
        self.count_lbl = tk.Label(res_hdr, text="", font=self.f, bg=BG, fg=FG2)
        self.count_lbl.pack(side="left", padx=10)
        self.send_btn = self._btn(res_hdr, "✈  Slack 전송", self._send_slack, self.f, accent=True)
        self.send_btn.pack(side="right")
        self._btn(res_hdr, "복사", self._copy, self.f).pack(side="right", padx=(0, 8))

        # 필터 검색창
        filter_row = tk.Frame(body, bg=BG)
        filter_row.pack(fill="x", pady=(0, 6))
        tk.Label(filter_row, text="담당자", font=self.f, bg=BG, fg=FG2, width=6, anchor="w").pack(side="left")
        self.filter_assignee_var = tk.StringVar()
        self.filter_assignee_var.trace_add("write", lambda *_: self._apply_filter())
        self._entry(filter_row, self.filter_assignee_var, self.fmono).pack(side="left", fill="x", expand=True, padx=(4, 14))
        tk.Label(filter_row, text="제목", font=self.f, bg=BG, fg=FG2, width=4, anchor="w").pack(side="left")
        self.filter_title_var = tk.StringVar()
        self.filter_title_var.trace_add("write", lambda *_: self._apply_filter())
        self._entry(filter_row, self.filter_title_var, self.fmono).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._btn(filter_row, "초기화", self._clear_filter, self.f).pack(side="right", padx=(8, 0))

        txt_wrap = tk.Frame(body, bg=BG3, highlightthickness=1, highlightbackground=LINE)
        txt_wrap.pack(fill="both", expand=True)
        self.result_text = tk.Text(txt_wrap, font=self.fmono, wrap="word",
                                   bg=BG3, fg=FG, insertbackground=FG,
                                   relief="flat", bd=0, padx=8, pady=8, state="disabled")
        sb = tk.Scrollbar(txt_wrap, command=self.result_text.yview, bg=BG2, troughcolor=BG)
        self.result_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.result_text.pack(fill="both", expand=True)

    # ── 멘션 설정 탭 ──────────────────────────────────────────────────────────

    def _build_mention_tab(self, parent):
        body = tk.Frame(parent, bg=BG, padx=14, pady=12)
        body.pack(fill="both", expand=True)

        desc = tk.Frame(body, bg=BG)
        desc.pack(fill="x", pady=(0, 8))
        tk.Label(desc, text="담당자별 Slack 멘션 매핑",
                 font=self.fs, bg=BG, fg=FG2).pack(side="left")
        tk.Label(desc, text="조회 시 UID 없는 담당자는 자동 추가됩니다",
                 font=self.f, bg=BG, fg=FG2).pack(side="right")

        hdr = tk.Frame(body, bg=BG2)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Jira 표시명", font=self.fb, bg=BG2, fg=FG,
                 width=22, anchor="w", padx=8, pady=5).pack(side="left")
        tk.Frame(hdr, bg=LINE, width=1).pack(side="left", fill="y")
        tk.Label(hdr, text="Slack User ID  (예: U012AB3CD)", font=self.fb,
                 bg=BG2, fg=FG, anchor="w", padx=8, pady=5).pack(side="left", fill="x", expand=True)

        outer = tk.Frame(body, bg=BG3, highlightthickness=1, highlightbackground=LINE)
        outer.pack(fill="both", expand=True, pady=(0, 8))

        canvas = tk.Canvas(outer, bg=BG3, highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview, bg=BG2, troughcolor=BG)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self.mention_frame = tk.Frame(canvas, bg=BG3)
        self.mention_window = canvas.create_window((0, 0), window=self.mention_frame, anchor="nw")

        def _on_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(self.mention_window, width=canvas.winfo_width())

        self.mention_frame.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(self.mention_window, width=e.width))

        self._mention_rows = []
        self._mention_canvas = canvas

        btn_row = tk.Frame(body, bg=BG)
        btn_row.pack(fill="x")
        self._btn(btn_row, "저장", self._save_mentions, self.f, accent=True).pack(side="right")

        self._reload_mention_rows()

    # ── 텍스트→JQL 탭 ────────────────────────────────────────────────────────

    def _build_text2jql_tab(self, parent):
        body = tk.Frame(parent, bg=BG, padx=14, pady=12)
        body.pack(fill="both", expand=True)

        # 이슈 prefix 설정
        prefix_row = tk.Frame(body, bg=BG)
        prefix_row.pack(fill="x", pady=(0, 10))
        tk.Label(prefix_row, text="이슈 키 prefix", font=self.fs, bg=BG, fg=FG2, width=14, anchor="w").pack(side="left")
        self.prefix_var = tk.StringVar(value=self.cfg.get("issue_prefix", ""))
        e = self._entry(prefix_row, self.prefix_var, self.fmono)
        e.configure(width=8)
        e.pack(side="left")
        self._btn(prefix_row, "저장", self._save_prefix, self.f).pack(side="left", padx=(8, 0))
        tk.Label(prefix_row, text="예: AB, CD, EF", font=self.f, bg=BG, fg=FG2).pack(side="left", padx=(10, 0))

        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=(0, 10))

        # 입력 영역
        tk.Label(body, text="텍스트 붙여넣기", font=self.fs, bg=BG, fg=FG2).pack(anchor="w", pady=(0, 4))
        input_wrap = tk.Frame(body, bg=BG3, highlightthickness=1, highlightbackground=LINE)
        input_wrap.pack(fill="both", expand=True, pady=(0, 8))
        self.t2j_input = tk.Text(input_wrap, font=self.fmono, wrap="word",
                                  bg=BG3, fg=FG, insertbackground=FG,
                                  relief="flat", bd=0, padx=8, pady=8, height=8)
        sb_in = tk.Scrollbar(input_wrap, command=self.t2j_input.yview, bg=BG2, troughcolor=BG)
        self.t2j_input.configure(yscrollcommand=sb_in.set)
        sb_in.pack(side="right", fill="y")
        self.t2j_input.pack(fill="both", expand=True)

        # 변환 버튼
        btn_row = tk.Frame(body, bg=BG)
        btn_row.pack(fill="x", pady=(0, 8))
        self.t2j_count_lbl = tk.Label(btn_row, text="", font=self.f, bg=BG, fg=FG2)
        self.t2j_count_lbl.pack(side="left")
        self._btn(btn_row, "초기화", self._t2j_clear, self.f).pack(side="right", padx=(8, 0))
        self._btn(btn_row, "▶  변환", self._t2j_convert, self.fb, accent=True).pack(side="right")

        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=(0, 8))

        # 결과 영역
        tk.Label(body, text="결과", font=self.fs, bg=BG, fg=FG2).pack(anchor="w", pady=(0, 4))

        # JQL 결과
        jql_hdr = tk.Frame(body, bg=BG)
        jql_hdr.pack(fill="x", pady=(0, 4))
        tk.Label(jql_hdr, text="JQL", font=self.fb, bg=BG, fg=FG2).pack(side="left")
        self._btn(jql_hdr, "복사", lambda: self._t2j_copy("jql"), self.f).pack(side="right")
        self._btn(jql_hdr, "조회 탭에 적용", self._t2j_apply_jql, self.f).pack(side="right", padx=(0, 8))

        jql_wrap = tk.Frame(body, bg=BG3, highlightthickness=1, highlightbackground=LINE)
        jql_wrap.pack(fill="x", pady=(0, 8))
        self.t2j_jql_text = tk.Text(jql_wrap, font=self.fmono, wrap="word",
                                     bg=BG3, fg=ACC, insertbackground=FG,
                                     relief="flat", bd=0, padx=8, pady=6, height=3, state="disabled")
        self.t2j_jql_text.pack(fill="x")

        # 링크 결과
        link_hdr = tk.Frame(body, bg=BG)
        link_hdr.pack(fill="x", pady=(0, 4))
        tk.Label(link_hdr, text="링크 목록", font=self.fb, bg=BG, fg=FG2).pack(side="left")
        self._btn(link_hdr, "복사", lambda: self._t2j_copy("links"), self.f).pack(side="right")

        link_wrap = tk.Frame(body, bg=BG3, highlightthickness=1, highlightbackground=LINE)
        link_wrap.pack(fill="both", expand=True)
        self.t2j_link_text = tk.Text(link_wrap, font=self.fmono, wrap="word",
                                      bg=BG3, fg=FG, insertbackground=FG,
                                      relief="flat", bd=0, padx=8, pady=8, state="disabled")
        sb_out = tk.Scrollbar(link_wrap, command=self.t2j_link_text.yview, bg=BG2, troughcolor=BG)
        self.t2j_link_text.configure(yscrollcommand=sb_out.set)
        sb_out.pack(side="right", fill="y")
        self.t2j_link_text.pack(fill="both", expand=True)

    # ── 설정 탭 ───────────────────────────────────────────────────────────────

    def _build_settings_tab(self, parent):
        body = tk.Frame(parent, bg=BG, padx=14, pady=12)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="Slack 전송 설정", font=self.fs, bg=BG, fg=FG2).pack(anchor="w", pady=(0, 4))

        row0 = tk.Frame(body, bg=BG)
        row0.pack(fill="x", pady=(0, 6))
        tk.Label(row0, text="Bot Token", font=self.f, bg=BG, fg=FG2, width=12, anchor="w").pack(side="left")
        self.slack_token_var = tk.StringVar(value=self.cfg.get("slack_bot_token", ""))
        self._entry(row0, self.slack_token_var, self.fmono, show="•").pack(side="left", fill="x", expand=True)

        row1 = tk.Frame(body, bg=BG)
        row1.pack(fill="x", pady=(0, 6))
        tk.Label(row1, text="내 User ID", font=self.f, bg=BG, fg=FG2, width=12, anchor="w").pack(side="left")
        self.slack_uid_var = tk.StringVar(value=self.cfg.get("slack_my_uid", ""))
        self._entry(row1, self.slack_uid_var, self.fmono).pack(side="left", fill="x", expand=True)

        hint = tk.Frame(body, bg=BG)
        hint.pack(fill="x", pady=(0, 12))
        tk.Label(hint,
                 text="Bot Token: api.slack.com → 앱 → OAuth & Permissions → Bot User OAuth Token (xoxb-...)\n"
                      "내 User ID: Slack 프로필 클릭 → 멤버 ID 복사 (U로 시작)",
                 font=self.f, bg=BG, fg=FG2, justify="left").pack(anchor="w")

        self._btn(body, "저장", self._save_slack_settings, self.f, accent=True).pack(anchor="e")

    # ── 멘션 관련 ─────────────────────────────────────────────────────────────

    def _reload_mention_rows(self):
        for w in self.mention_frame.winfo_children():
            w.destroy()
        self._mention_rows.clear()
        mentions = self.cfg.get("slack_mentions", {})
        for name, uid in mentions.items():
            self._add_mention_row(name, uid)
        if not mentions:
            tk.Label(self.mention_frame, text="조회를 실행하면 담당자가 자동으로 추가됩니다",
                     font=self.f, bg=BG3, fg=FG2, pady=16).pack()

    def _add_mention_row(self, name="", uid=""):
        for w in self.mention_frame.winfo_children():
            if isinstance(w, tk.Label):
                w.destroy()

        row = tk.Frame(self.mention_frame, bg=BG3)
        row.pack(fill="x")
        tk.Frame(self.mention_frame, bg=LINE, height=1).pack(fill="x")

        name_var = tk.StringVar(value=name)
        uid_var  = tk.StringVar(value=uid)

        ne = tk.Entry(row, textvariable=name_var, font=self.fmono,
                      bg=BG3, fg=FG, insertbackground=FG,
                      disabledforeground=FG, readonlybackground=BG3,
                      relief="flat", bd=0, width=22, highlightthickness=0)
        ne.pack(side="left", padx=(8, 0), pady=5)
        ne.configure(state="readonly")

        tk.Frame(row, bg=LINE, width=1).pack(side="left", fill="y", padx=(6, 0))

        tk.Entry(row, textvariable=uid_var, font=self.fmono,
                 bg=BG3, fg=ACC, insertbackground=FG,
                 relief="flat", bd=0, highlightthickness=0).pack(
                     side="left", fill="x", expand=True, padx=8, pady=5)

        self._mention_rows.append((name_var, uid_var))

    def _save_mentions(self):
        mentions = {nv.get().strip(): uv.get().strip()
                    for nv, uv in self._mention_rows if nv.get().strip()}
        self.cfg["slack_mentions"] = mentions
        save_config(self.cfg)
        self._set_status("✓ 멘션 저장됨", OK)

    def _auto_add_assignees(self, names):
        mentions = self.cfg.get("slack_mentions", {})
        added = any(n not in mentions for n in names)
        for n in names:
            mentions.setdefault(n, "")
        if added:
            self.cfg["slack_mentions"] = mentions
            save_config(self.cfg)
            self._reload_mention_rows()

    # ── 헬퍼 ──────────────────────────────────────────────────────────────────

    def _entry(self, parent, var, font, show=None):
        kw = dict(textvariable=var, font=font, bg=BG3, fg=FG,
                  insertbackground=FG, relief="flat", bd=0,
                  highlightthickness=1, highlightbackground=LINE, highlightcolor=ACC)
        if show:
            kw["show"] = show
        return tk.Entry(parent, **kw)

    def _btn(self, parent, text, cmd, font=None, accent=False):
        bg = ACC if accent else BG2
        fg = "#ffffff" if accent else FG
        return tk.Button(parent, text=text, command=cmd, font=font,
                         bg=bg, fg=fg, activebackground=BG2,
                         activeforeground=FG, relief="flat", bd=0,
                         padx=14, pady=5, cursor="hand2")

    # ── 동작 ──────────────────────────────────────────────────────────────────

    def _save_auth(self):
        self.cfg.update({
            "domain":    self.domain_var.get().strip(),
            "email":     self.email_var.get().strip(),
            "api_token": self.token_var.get().strip(),
        })
        save_config(self.cfg)
        self._set_status("✓ 저장됨", OK)

    def _save_slack_settings(self):
        self.cfg.update({
            "slack_bot_token": self.slack_token_var.get().strip(),
            "slack_my_uid":    self.slack_uid_var.get().strip(),
        })
        save_config(self.cfg)
        self._set_status("✓ Slack 설정 저장됨", OK)

    def _run(self):
        domain = self.domain_var.get().strip()
        email  = self.email_var.get().strip()
        token  = self.token_var.get().strip()
        jql    = self.jql_text.get("1.0", "end").strip()
        fmt    = self.fmt_var.get()
        if not all([domain, email, token, jql]):
            messagebox.showwarning("입력 확인", "도메인, 이메일, API 토큰, JQL을 모두 입력해 주세요.")
            return
        self.cfg.update({"last_jql": jql, "fmt": fmt})
        # 히스토리 저장 (최대 10개, 중복 제거 후 가장 최근이 뒤로)
        import datetime
        ts = datetime.datetime.now().strftime("%y-%m-%d %H:%M:%S")
        history = self.cfg.get("jql_history", [])
        # 동일 JQL 있으면 제거
        history = [h for h in history if h.get("jql") != jql]
        history.append({"jql": jql, "ts": ts})
        self.cfg["jql_history"] = history[-10:]
        save_config(self.cfg)
        self.run_btn.configure(state="disabled", text="조회 중…")
        self._set_status("", "")
        self._set_result("")
        self.count_lbl.configure(text="")

        def worker():
            try:
                issues = fetch_issues(domain, email, token, jql)
                if not issues:
                    self.after(0, lambda: self._set_result("(조건에 맞는 이슈 없음)"))
                    self.after(0, lambda: self._set_status("0건", FG2))
                    return
                mentions = self.cfg.get("slack_mentions", {})
                self._last_issues = issues
                self.after(0, self._clear_filter)
                grouping = self.grouping_var.get()
                result, ti, ta, names = group_and_format(issues, domain, fmt, mentions, grouping)
                count_txt = f"총 {ti}건  /  담당자 {ta}명" if grouping else f"총 {ti}건"
                self.after(0, lambda: self._set_result(result))
                self.after(0, lambda: self.count_lbl.configure(text=count_txt))
                self.after(0, lambda: self._set_status(f"완료 ({ti}건)", OK))
                self.after(0, lambda: self._auto_add_assignees(names))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self._set_result(f"오류:\n{msg}"))
                self.after(0, lambda: self._set_status("오류", ERR))
            finally:
                self.after(0, lambda: self.run_btn.configure(state="normal", text="▶  조회"))

        threading.Thread(target=worker, daemon=True).start()

    def _send_slack(self):
        if not hasattr(self, "_last_issues") or not self._last_issues:
            messagebox.showwarning("전송 오류", "먼저 조회를 실행해 주세요.")
            return
        bot_token = self.cfg.get("slack_bot_token", "").strip()
        my_uid    = self.cfg.get("slack_my_uid", "").strip()
        if not bot_token or not my_uid:
            messagebox.showwarning("설정 필요", "설정 탭에서 Bot Token과 내 User ID를 입력해 주세요.")
            return

        domain          = self.domain_var.get().strip()
        jql             = self.jql_text.get("1.0", "end").strip()
        mentions        = self.cfg.get("slack_mentions", {})
        filtered        = self._filtered_issues()
        grouping        = self.grouping_var.get()
        assignee_blocks = build_slack_blocks(filtered, domain, mentions, grouping)
        total_issues    = len(filtered)
        total_assignees = len(assignee_blocks) if grouping else 0

        self.send_btn.configure(state="disabled", text="전송 중…")

        def worker():
            try:
                send_slack_dm(bot_token, my_uid, assignee_blocks, total_issues, total_assignees, jql, grouping)
                self.after(0, lambda: self._set_status("✓ Slack DM 전송 완료", OK))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self._set_status(f"전송 실패: {msg}", ERR))
                self.after(0, lambda: messagebox.showerror("전송 실패", msg))
            finally:
                self.after(0, lambda: self.send_btn.configure(state="normal", text="✈  Slack 전송"))

        threading.Thread(target=worker, daemon=True).start()

    def _filtered_issues(self):
        """현재 필터 조건에 맞는 이슈 반환"""
        if not hasattr(self, "_last_issues") or not self._last_issues:
            return []
        assignee_kw = self.filter_assignee_var.get().strip().lower()
        title_kw    = self.filter_title_var.get().strip().lower()
        result = []
        for iss in self._last_issues:
            assignee = (iss["fields"].get("assignee") or {}).get("displayName", "미배정").lower()
            summary  = iss["fields"].get("summary", "").lower()
            if assignee_kw and assignee_kw not in assignee:
                continue
            if title_kw and title_kw not in summary:
                continue
            result.append(iss)
        return result

    def _apply_filter(self):
        """필터 변경 시 결과창 즉시 갱신"""
        if not hasattr(self, "_last_issues") or not self._last_issues:
            return
        domain   = self.domain_var.get().strip()
        fmt      = self.fmt_var.get()
        mentions = self.cfg.get("slack_mentions", {})
        filtered = self._filtered_issues()
        if not filtered:
            self._set_result("(필터 조건에 맞는 이슈 없음)")
            self.count_lbl.configure(text="0건")
            return
        grouping = self.grouping_var.get()
        result, ti, ta, _ = group_and_format(filtered, domain, fmt, mentions, grouping)
        self._set_result(result)
        total = len(self._last_issues)
        suffix = f"  (전체 {total}건)" if ti < total else ""
        count_txt = f"총 {ti}건  /  담당자 {ta}명{suffix}" if grouping else f"총 {ti}건{suffix}"
        self.count_lbl.configure(text=count_txt)

    def _show_history_menu(self):
        history = self.cfg.get("jql_history", [])
        if not history:
            self._set_status("저장된 JQL 기록 없음", FG2)
            return
        menu = tk.Menu(self, tearoff=0, bg=BG2, fg=FG, activebackground=ACC,
                       activeforeground="#fff", font=self.fmono,
                       relief="flat", bd=0)
        # 오래된 것부터 1번, 가장 최근이 마지막 번호
        for idx, entry in enumerate(history, 1):
            if isinstance(entry, dict):
                jql = entry.get("jql", "")
                ts  = entry.get("ts", "")
            else:
                jql, ts = entry, ""
            jql_preview = jql if len(jql) <= 50 else jql[:47] + "..."
            label = f"{idx}. ({ts})  {jql_preview}" if ts else f"{idx}. {jql_preview}"
            menu.add_command(label=label, command=lambda j=jql: self._apply_history(j))
        menu.add_separator()
        menu.add_command(label="기록 전체 삭제", command=self._clear_history)
        x = self.history_btn.winfo_rootx()
        y = self.history_btn.winfo_rooty() + self.history_btn.winfo_height()
        menu.tk_popup(x, y)

    def _apply_history(self, jql):
        self.jql_text.delete("1.0", "end")
        self.jql_text.insert("1.0", jql)

    def _clear_history(self):
        self.cfg["jql_history"] = []
        save_config(self.cfg)
        self._set_status("JQL 기록 삭제됨", OK)

    def _save_prefix(self):
        self.cfg["issue_prefix"] = self.prefix_var.get().strip().upper()
        save_config(self.cfg)
        self._set_status("✓ prefix 저장됨", OK)

    def _t2j_convert(self):
        import re
        raw = self.t2j_input.get("1.0", "end")
        prefix = self.prefix_var.get().strip().upper()
        if not prefix:
            self.t2j_count_lbl.configure(text="prefix를 먼저 입력해 주세요")
            return
        pattern = rf"{re.escape(prefix)}-\d{{5}}"
        keys = list(dict.fromkeys(re.findall(pattern, raw)))  # 중복 제거, 순서 유지
        if not keys:
            self.t2j_count_lbl.configure(text=f"'{prefix}-XXXXX' 패턴을 찾지 못했습니다")
            self._t2j_set("jql", "")
            self._t2j_set("links", "")
            return
        domain = self.domain_var.get().strip()
        base   = f"https://{domain}/browse/"
        jql    = f"issue in ({', '.join(keys)})"
        links  = "\n".join(f"{k}: {base}{k}" for k in keys)
        self._t2j_set("jql", jql)
        self._t2j_set("links", links)
        self.t2j_count_lbl.configure(text=f"{len(keys)}개 이슈 키 추출됨")

    def _t2j_set(self, target, text):
        w = self.t2j_jql_text if target == "jql" else self.t2j_link_text
        w.configure(state="normal")
        w.delete("1.0", "end")
        w.insert("1.0", text)
        w.configure(state="disabled")

    def _t2j_copy(self, target):
        w = self.t2j_jql_text if target == "jql" else self.t2j_link_text
        txt = w.get("1.0", "end").strip()
        if not txt:
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        self._set_status("클립보드에 복사됨", OK)

    def _t2j_apply_jql(self):
        jql = self.t2j_jql_text.get("1.0", "end").strip()
        if not jql:
            return
        self.jql_text.delete("1.0", "end")
        self.jql_text.insert("1.0", jql)
        self._set_status("✓ JQL 적용됨 — 조회 탭에서 실행하세요", OK)

    def _t2j_clear(self):
        self.t2j_input.delete("1.0", "end")
        self._t2j_set("jql", "")
        self._t2j_set("links", "")
        self.t2j_count_lbl.configure(text="")

    def _clear_filter(self):
        self.filter_assignee_var.set("")
        self.filter_title_var.set("")

    def _copy(self):
        txt = self.result_text.get("1.0", "end").strip()
        if not txt:
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        self._set_status("클립보드에 복사됨", OK)

    def _set_result(self, text):
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)
        self.result_text.configure(state="disabled")

    def _set_status(self, text, color):
        self.status_lbl.configure(text=text, fg=color or FG2)


if __name__ == "__main__":
    App().mainloop()
