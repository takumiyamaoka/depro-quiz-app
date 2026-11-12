"""DE Pro quiz app (Databricks Apps / Streamlit, phone-first single column)."""
import os
import uuid
from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config

SCHEMA = os.getenv("QUIZ_SCHEMA", "workspace.depro_quiz")
JST = timezone(timedelta(hours=9))
LETTERS = ["A", "B", "C", "D", "E"]
CONFIDENCE = {"自信あり": "sure", "迷った": "unsure", "勘": "guess"}
SECTIONS = {
    "S1": "Python/SQL開発", "S2": "取り込み", "S3": "データ変換", "S4": "監視",
    "S5": "コスト・性能", "S6": "セキュリティ", "S7": "ガバナンス",
    "S8": "デバッグ・デプロイ", "S9": "モデリング",
}

st.set_page_config(page_title="DE Pro Drill", page_icon="📘", layout="centered")
st.markdown(
    """<style>
    div.block-container{padding-top:3rem;padding-bottom:6rem;max-width:720px}
    div[role=radiogroup] label{padding:.35rem 0}
    /* bottom dock */
    .st-key-dock{position:fixed;left:0;right:0;bottom:0;z-index:999;
      background:var(--background-color, #fff);border-top:1px solid rgba(128,128,128,.3);
      padding:.4rem .75rem calc(.4rem + env(safe-area-inset-bottom));}
    @media (prefers-color-scheme: dark){.st-key-dock{background:#0e1117}}
    .st-key-dock div[data-testid="stHorizontalBlock"], .st-key-kpi div[data-testid="stHorizontalBlock"]
      {flex-wrap:nowrap !important;gap:.4rem;max-width:720px;margin:0 auto}
    .st-key-dock div[data-testid="stColumn"], .st-key-kpi div[data-testid="stColumn"]
      {min-width:0 !important;flex:1 1 0 !important}
    .st-key-kpi [data-testid="stMetricValue"]{font-size:1.6rem}
    .st-key-kpi [data-testid="stMetric"]{border:1px solid rgba(128,128,128,.25);border-radius:.5rem;padding:.4rem .6rem}
    .st-key-dock button{padding:.35rem 0;line-height:1.2;white-space:pre-line}
    </style>""",
    unsafe_allow_html=True,
)


# ---------- data access ----------
@st.cache_resource
def _conn():
    cfg = Config()  # app service principal (injected by Databricks Apps)
    return sql.connect(
        server_hostname=cfg.host,
        http_path=f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}",
        credentials_provider=lambda: cfg.authenticate,
    )


def query(q: str, params: dict | None = None) -> pd.DataFrame:
    with _conn().cursor() as cur:
        cur.execute(q, params or {})
        return cur.fetchall_arrow().to_pandas()


def execute(q: str, params: dict | None = None) -> None:
    with _conn().cursor() as cur:
        cur.execute(q, params or {})


def load_questions(mode: str, section: str | None) -> pd.DataFrame:
    today = datetime.now(JST).date().isoformat()
    if mode == "today":
        return query(
            f"""SELECT q.* FROM {SCHEMA}.questions q
                WHERE q.drill_date = (SELECT max(drill_date) FROM {SCHEMA}.questions
                                      WHERE drill_date <= :today)
                  AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.attempts a
                                  WHERE a.question_id = q.question_id)
                ORDER BY q.question_id""",
            {"today": today},
        )
    if mode == "review":
        return query(
            f"""SELECT q.* FROM {SCHEMA}.review_queue r
                JOIN {SCHEMA}.questions q USING (question_id)
                ORDER BY r.priority, r.last_answered_at NULLS FIRST, rand() LIMIT :n""",
            {"n": settings()["review_n"]},
        )
    if mode == "section":
        return query(
            f"SELECT * FROM {SCHEMA}.questions WHERE section = :s ORDER BY rand() LIMIT :n",
            {"s": section, "n": settings()["random_n"]},
        )
    return query(f"SELECT * FROM {SCHEMA}.questions ORDER BY rand() LIMIT :n",
                 {"n": settings()["random_n"]})


def save_attempt(qid: str, chosen: str, correct: bool, confidence: str, mode: str) -> None:
    execute(
        f"""INSERT INTO {SCHEMA}.attempts
            (attempt_id, question_id, chosen, is_correct, confidence, mode, answered_at)
            VALUES (:id, :qid, :chosen, :ok, :conf, :mode, current_timestamp())""",
        {"id": str(uuid.uuid4()), "qid": qid, "chosen": chosen, "ok": correct,
         "conf": confidence, "mode": mode},
    )


# ---------- settings (per user, persisted in user_settings) ----------
DEFAULT_SETTINGS = {"review_n": 10, "random_n": 10, "require_confidence": False}


def settings() -> dict:
    if "settings" not in st.session_state:
        cur = dict(DEFAULT_SETTINGS)
        try:
            df = query(f"SELECT * FROM {SCHEMA}.user_settings WHERE user_email = :u",
                       {"u": user_email()})
            if not df.empty:
                r = df.iloc[0]
                cur.update(review_n=int(r["review_n"]), random_n=int(r["random_n"]),
                           require_confidence=bool(r["require_confidence"]))
        except Exception:
            pass
        st.session_state.settings = cur
    return st.session_state.settings


def save_settings(new: dict) -> None:
    execute(
        f"""MERGE INTO {SCHEMA}.user_settings t
            USING (SELECT :u AS user_email, :r AS review_n, :x AS random_n, :c AS require_confidence) s
            ON t.user_email = s.user_email
            WHEN MATCHED THEN UPDATE SET review_n = s.review_n, random_n = s.random_n,
                 require_confidence = s.require_confidence, updated_at = current_timestamp()
            WHEN NOT MATCHED THEN INSERT (user_email, review_n, random_n, require_confidence, updated_at)
                 VALUES (s.user_email, s.review_n, s.random_n, s.require_confidence, current_timestamp())""",
        {"u": user_email(), "r": int(new["review_n"]), "x": int(new["random_n"]),
         "c": bool(new["require_confidence"])},
    )
    st.session_state.settings = dict(new)


# ---------- quiz state (persisted in quiz_sessions so a set can be resumed) ----------
MODE_LABEL = {"today": "今日のドリル", "review": "復習", "section": "セクション指定", "random": "ランダム"}


def user_email() -> str:
    try:
        return st.context.headers.get("X-Forwarded-Email") or "unknown"
    except Exception:
        return "unknown"


def save_progress(status: str = "active") -> None:
    ss = st.session_state
    execute(
        f"""UPDATE {SCHEMA}.quiz_sessions
            SET idx = :idx, score = :score, status = :status, updated_at = current_timestamp()
            WHERE session_id = :sid""",
        {"idx": ss.saved_idx, "score": ss.score, "status": status, "sid": ss.sid},
    )


def start_set(mode: str, section: str | None = None) -> None:
    qs = load_questions(mode, section).to_dict("records")
    sid = str(uuid.uuid4())
    email = user_email()
    execute(
        f"""UPDATE {SCHEMA}.quiz_sessions SET status = 'abandoned', updated_at = current_timestamp()
            WHERE user_email = :u AND status = 'active'""",
        {"u": email},
    )
    if qs:
        execute(
            f"""INSERT INTO {SCHEMA}.quiz_sessions
                (session_id, user_email, mode, question_ids, total, idx, score, status, started_at, updated_at)
                VALUES (:sid, :u, :mode, :ids, :total, 0, 0, 'active', current_timestamp(), current_timestamp())""",
            {"sid": sid, "u": email, "mode": mode,
             "ids": ",".join(q["question_id"] for q in qs), "total": len(qs)},
        )
    st.session_state.update(qs=qs, idx=0, saved_idx=0, answered=None, score=0, mode=mode, sid=sid)


def active_session() -> dict | None:
    df = query(
        f"""SELECT * FROM {SCHEMA}.quiz_sessions
            WHERE user_email = :u AND status = 'active' AND idx < total
            ORDER BY updated_at DESC LIMIT 1""",
        {"u": user_email()},
    )
    return None if df.empty else df.iloc[0].to_dict()


def resume_set(sess: dict) -> None:
    ids = sess["question_ids"].split(",")
    df = query(
        f"SELECT * FROM {SCHEMA}.questions WHERE array_contains(split(:ids, ','), question_id)",
        {"ids": sess["question_ids"]},
    )
    by_id = {r["question_id"]: r for r in df.to_dict("records")}
    qs = [by_id[i] for i in ids if i in by_id]
    st.session_state.update(qs=qs, idx=int(sess["idx"]), saved_idx=int(sess["idx"]),
                            answered=None, score=int(sess["score"]), mode=sess["mode"],
                            sid=sess["session_id"])


def back_to_menu() -> None:
    for k in ("qs", "idx", "saved_idx", "answered", "score", "mode", "sid"):
        st.session_state.pop(k, None)


def quiz_view() -> None:
    qs = st.session_state.get("qs")
    if qs is None:
        return
    if st.button("‹ 戻る", key="back"):
        back_to_menu()
        st.rerun()
    if not qs:
        st.info("出題できる問題がありません。")
        return
    idx = st.session_state.idx
    if idx >= len(qs):
        st.success(f"終了：{st.session_state.score} / {len(qs)} 問正解")
        if st.button("メニューに戻る", use_container_width=True):
            back_to_menu()
            st.rerun()
        return

    q = qs[idx]
    st.caption(f"{MODE_LABEL.get(st.session_state.mode, '')}　{idx + 1} / {len(qs)}　"
               f"{q['section']} {SECTIONS.get(q['section'], '')}・{q['topic']}")
    st.markdown(f"**{q['stem']}**")
    choices = list(q["choices"])
    labels = [f"{LETTERS[i]}. {c}" for i, c in enumerate(choices)]
    done = st.session_state.answered is not None

    pick = st.radio("選択肢", labels, index=None, key=f"pick_{q['question_id']}",
                    label_visibility="collapsed", disabled=done)
    conf = st.radio("自信度", list(CONFIDENCE), horizontal=True,
                    index=None if settings()["require_confidence"] else 0,
                    key=f"conf_{q['question_id']}", disabled=done)

    if not done:
        if st.button("回答する", type="primary", use_container_width=True, disabled=pick is None or conf is None):
            chosen = pick.split(".")[0]
            ok = chosen == q["answer"]
            save_attempt(q["question_id"], chosen, ok, CONFIDENCE[conf], st.session_state.mode)
            st.session_state.answered = chosen
            st.session_state.score += int(ok)
            st.session_state.saved_idx = idx + 1
            save_progress("done" if idx + 1 >= len(qs) else "active")
            st.rerun()
        return

    chosen = st.session_state.answered
    if chosen == q["answer"]:
        st.success(f"正解（{chosen}）")
    else:
        st.error(f"不正解：あなた {chosen} / 正解 {q['answer']}")
    st.markdown(q.get("explanation_ja") or "")
    if q.get("doc_url"):
        st.markdown(f"[公式ドキュメント]({q['doc_url']})")
    if st.button("次へ", type="primary", use_container_width=True):
        st.session_state.idx += 1
        st.session_state.answered = None
        st.rerun()


# ---------- pages ----------
PAGES = {"quiz": ("📝", "Quiz"), "dashboard": ("📊", "Dashboard"), "setting": ("⚙️", "Setting")}
st.session_state.setdefault("page", "quiz")


def page_quiz() -> None:
    if st.session_state.get("qs") is None:
        sess = active_session()
        if sess:
            label = (f"続きから再開：{MODE_LABEL.get(sess['mode'], '')} "
                     f"{int(sess['idx']) + 1} / {int(sess['total'])}問目")
            if st.button(label, type="primary", use_container_width=True):
                resume_set(sess); st.rerun()
            st.caption("新しく始めると、途中のセットは破棄されます。")
        c1, c2 = st.columns(2)
        if c1.button("今日のドリル", type="secondary" if sess else "primary",
                     use_container_width=True):
            start_set("today"); st.rerun()
        if c2.button("復習", use_container_width=True):
            start_set("review"); st.rerun()
        sec = st.selectbox("セクション指定", list(SECTIONS),
                           format_func=lambda s: f"{s} {SECTIONS[s]}")
        c3, c4 = st.columns(2)
        if c3.button("このセクションを解く", use_container_width=True):
            start_set("section", sec); st.rerun()
        if c4.button(f"ランダム{settings()['random_n']}問", use_container_width=True):
            start_set("random"); st.rerun()
    quiz_view()


def _pct(x) -> str:
    return "-" if x is None or pd.isna(x) else f"{float(x) * 100:.0f}%"


def page_dashboard() -> None:
    st.subheader("ドリルの実施状況")
    drills = query(
        f"""WITH first_try AS (
              SELECT * FROM {SCHEMA}.attempts
              QUALIFY row_number() OVER (PARTITION BY question_id ORDER BY answered_at) = 1)
            SELECT q.drill_date,
                   count(*)                     AS total,
                   count(f.question_id)         AS answered,
                   count_if(f.is_correct)       AS correct
            FROM {SCHEMA}.questions q LEFT JOIN first_try f USING (question_id)
            WHERE q.source = 'daily'
            GROUP BY q.drill_date ORDER BY q.drill_date DESC"""
    )
    days = query(
        f"""SELECT DISTINCT to_date(from_utc_timestamp(answered_at, 'Asia/Tokyo')) AS d
            FROM {SCHEMA}.attempts ORDER BY d DESC"""
    )
    overall = query(
        f"SELECT MEASURE(answers) AS answers, MEASURE(accuracy) AS accuracy FROM {SCHEMA}.quiz_metrics"
    )

    # streak of consecutive study days (JST) ending today or yesterday
    today = datetime.now(JST).date()
    streak, expect = 0, today
    for d in pd.to_datetime(days["d"]).dt.date if not days.empty else []:
        if d == expect or (streak == 0 and d == today - timedelta(days=1)):
            streak += 1
            expect = d - timedelta(days=1)
        else:
            break

    latest = drills.iloc[0] if not drills.empty else None
    kpi = st.container(key="kpi")
    k1, k2 = kpi.columns(2)
    k1.metric("総回答数", int(overall["answers"].iloc[0] or 0))
    k2.metric("正答率", _pct(overall["accuracy"].iloc[0]))
    k3, k4 = kpi.columns(2)
    k3.metric("最新ドリル", "-" if latest is None else
              f"{int(latest['answered'])}/{int(latest['total'])}問",
              help=None if latest is None else f"{latest['drill_date']} 配信分")
    k4.metric("連続学習日数", f"{streak}日")

    if not drills.empty:
        view = pd.DataFrame({
            "日付": pd.to_datetime(drills["drill_date"]).dt.strftime("%m/%d"),
            "回答": drills["answered"].astype(int).astype(str) + "/" + drills["total"].astype(int).astype(str),
            "正解": drills["correct"].astype(int),
            "状態": ["✅" if a == t else ("…" if a else "未") for a, t in
                    zip(drills["answered"].astype(int), drills["total"].astype(int))],
        })
        st.dataframe(view, hide_index=True, use_container_width=True, height=min(36 * (len(view) + 1), 300))

    st.subheader("セクション別の正答率")
    by_sec = query(
        f"""SELECT section, MEASURE(answers) AS answers, MEASURE(accuracy) AS accuracy
            FROM {SCHEMA}.quiz_metrics GROUP BY section ORDER BY section"""
    )
    if by_sec.empty:
        st.info("まだ回答がありません。")
        return
    by_sec["正答率(%)"] = (by_sec["accuracy"].astype(float) * 100).round(0)
    by_sec["label"] = by_sec["section"].map(lambda s: f"{s} {SECTIONS.get(s, '')}")
    st.bar_chart(by_sec.set_index("label")["正答率(%)"], horizontal=True, height=260)
    st.caption("回答数：" + "、".join(f"{r.section} {int(r.answers)}" for r in by_sec.itertuples()))

    st.subheader("苦手トピック（上位5件）")
    weak = query(
        f"""SELECT topic, MEASURE(answers) AS answers, MEASURE(accuracy) AS accuracy
            FROM {SCHEMA}.quiz_metrics GROUP BY topic
            HAVING MEASURE(answers) >= 2 ORDER BY accuracy, answers DESC LIMIT 5"""
    )
    if weak.empty:
        st.caption("2回以上解いたトピックがまだありません。")
    else:
        weak["正答率"] = weak["accuracy"].map(_pct)
        st.dataframe(weak[["topic", "answers", "正答率"]].rename(columns={"topic": "トピック", "answers": "回答数"}),
                     hide_index=True, use_container_width=True)

    st.subheader("日別の正答率")
    daily = query(
        f"""SELECT answered_date, MEASURE(accuracy) AS accuracy, MEASURE(answers) AS answers
            FROM {SCHEMA}.quiz_metrics GROUP BY answered_date ORDER BY answered_date"""
    )
    if not daily.empty:
        st.line_chart(daily.set_index("answered_date")["accuracy"].astype(float) * 100, height=200)


def page_setting() -> None:
    cur = settings()
    st.subheader("出題")
    opts = [5, 10, 20]
    review_n = st.radio("復習の出題数", opts, index=opts.index(cur["review_n"]) if cur["review_n"] in opts else 1,
                        horizontal=True, format_func=lambda n: f"{n}問")
    random_n = st.radio("ランダム・セクション指定の出題数", opts,
                        index=opts.index(cur["random_n"]) if cur["random_n"] in opts else 1,
                        horizontal=True, format_func=lambda n: f"{n}問")
    st.subheader("回答")
    req = st.toggle("自信度を毎回選ぶ（初期値を「自信あり」にしない）", value=cur["require_confidence"],
                    help="オンにすると、自信度を選ぶまで「回答する」を押せません。勘で当たった問題を復習に回しやすくなります。")
    new = {"review_n": review_n, "random_n": random_n, "require_confidence": req}
    if st.button("保存", type="primary", use_container_width=True, disabled=new == cur):
        save_settings(new)
        st.success("保存しました。")
    st.divider()
    st.caption(f"ログイン中：{user_email()}")
    st.caption("アプリは6:00〜24:00の間、止まっても15分以内に自動で再起動します（夜1:00に停止）。")


page = st.session_state.page
{"quiz": page_quiz, "dashboard": page_dashboard, "setting": page_setting}[page]()

# dock (fixed to the bottom by CSS on .st-key-dock)
with st.container(key="dock"):
    cols = st.columns(len(PAGES))
    for col, (key, (icon, label)) in zip(cols, PAGES.items()):
        if col.button(f"{icon}\n{label}", key=f"dock_{key}", use_container_width=True,
                      type="primary" if key == page else "secondary"):
            if key != page:
                st.session_state.page = key
                st.rerun()
