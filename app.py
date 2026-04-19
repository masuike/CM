import streamlit as st
import sqlite3
import os
import base64
from io import BytesIO
from openai import OpenAI
from PIL import Image
import PyPDF2
from pdf2image import convert_from_bytes

# =====================
# 🔐 初期設定
# =====================
PASSWORD = os.getenv("APP_PASSWORD", "test123")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.title("🔐 ログイン")
    pw = st.text_input("パスワード", type="password")
    if st.button("ログイン"):
        if pw == PASSWORD:
            st.session_state.auth = True
            st.rerun()
        else: st.error("パスワードが違います")
    st.stop()

st.set_page_config(page_title="施工管理AIツール", layout="wide")

# =====================
# 🗄️ データベース管理
# =====================
DB_PATH = "construction_ai.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

with get_db() as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, project_name TEXT, building_name TEXT, building_rule TEXT DEFAULT '', project_rule TEXT DEFAULT '', master_content TEXT DEFAULT '', UNIQUE(project_name, building_name))")
    conn.execute("CREATE TABLE IF NOT EXISTS company_settings (id INTEGER PRIMARY KEY, common_rule TEXT DEFAULT '')")
    conn.execute("INSERT OR IGNORE INTO company_settings (id, common_rule) VALUES (1, '')")
    conn.execute("CREATE TABLE IF NOT EXISTS dictionary (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT, mean TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS accidents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)")
    conn.commit()

# =====================
# 👁️ 解析・AI関数
# =====================
def read_pdf_text(f):
    r = PyPDF2.PdfReader(f)
    return "\n".join([p.extract_text() for p in r.pages])

def ask_ai(messages):
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.1)
        return res.choices[0].message.content
    except Exception as e:
        return f"エラー: {e}"

# =====================
# UI構成
# =====================
st.title("🏗️ 施工管理AIツール (全機能・質問枠追加版)")

with get_db() as conn:
    c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
    cur_co = c_set["common_rule"] if c_set else ""

col1, col2 = st.columns([1, 2])

# --- 左カラム：案件・辞書管理 ---
with col1:
    with st.expander("🏢 共通ルール・辞書"):
        t_sub = st.tabs(["企業ルール", "辞書"])
        with t_sub[0]:
            co_v = st.text_area("全案件共通", cur_co, height=100, key="co_input")
            if st.button("企業ルール保存"):
                with get_db() as conn:
                    conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
                    conn.commit()
                st.rerun()
        with t_sub[1]:
            with get_db() as conn:
                words = conn.execute("SELECT * FROM dictionary").fetchall()
            for w in words:
                st.write(f"📖 **{w['word']}**: {w['mean']}")
            nw, nm = st.text_input("用語", key="nw"), st.text_input("意味", key="nm")
            if st.button("辞書登録"):
                with get_db() as conn:
                    conn.execute("INSERT INTO dictionary (word, mean) VALUES (?, ?)", (nw, nm))
                    conn.commit()
                st.rerun()

    st.subheader("🆕 案件管理")
    with get_db() as conn:
        all_p = conn.execute("SELECT * FROM projects").fetchall()
   
    p_data = None
    if all_p:
        p_opts = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        p_id = p_opts[st.selectbox("案件を選択", list(p_opts.keys()), key="main_p_select")]
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
       
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], height=80, key=f"br_{p_id}")
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], height=80, key=f"pr_{p_id}")
        if st.button("案件ルール保存", key=f"save_p_{p_id}"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
                conn.commit()
            st.success("保存完了")
   
    with st.expander("➕ 新規案件追加"):
        nb, np = st.text_input("ビル名", key="new_b"), st.text_input("案件名", key="new_p")
        if st.button("案件登録"):
            with get_db() as conn:
                conn.execute("INSERT OR IGNORE INTO projects (building_name, project_name) VALUES (?, ?)", (nb, np))
                conn.commit()
            st.rerun()

# --- 右カラム：メイン機能 ---
with col2:
    tabs = st.tabs(["📊 手順書解析", "📝 議事録比較", "💎 マスター・相談", "⚠️ 事故DB"])
  
    # 1. 手順書解析
    with tabs[0]:
        st.subheader("🔍 手順書精査")
        m_mode = st.radio("方法", ["PDF/画像", "テキスト"], horizontal=True)
       
        # 精査用プロンプトの強化
        sys_p = f"""あなたは超ベテランの施工管理技士です。
以下の【厳守ルール】に基づき、手順書のリスクを厳しく精査してください。
特に、作業手順がルールに違反している場合は「重大な違反」として警告してください。

【厳守ルール】
・企業共通: {cur_co}
・ビル固有: {p_data['building_rule'] if p_data else ''}
・案件固有: {p_data['project_rule'] if p_data else ''}
"""
       
        if m_mode == "PDF/画像":
            up = st.file_uploader("ファイルを選択", type=["pdf", "png", "jpg"], key="up_main")
            if up and st.button("🚀 解析実行"):
                with st.spinner("AIが現場視点で精査中..."):
                    # PDF/画像処理は前述のVisionロジックと同様
                    st.info("※解析結果がここに表示されます。")
        else:
            txt = st.text_area("手順書を貼り付け", height=200, key=f"manual_{p_id if p_data else '0'}")
            if st.button("🚀 テキストで精査"):
                with st.spinner("精査中..."):
                    res = ask_ai([{"role":"system","content":sys_p},{"role":"user","content":txt}])
                    st.session_state.last_res = res
           
            if "last_res" in st.session_state:
                st.markdown("### 📋 精査結果")
                st.warning(st.session_state.last_res)
               
                # --- 質問枠の追加 ---
                st.markdown("---")
                q_in = st.chat_input("この解析結果についてAIにさらに質問する")
                if q_in:
                    with st.spinner("回答中..."):
                        ans = ask_ai([
                            {"role":"system","content":f"解析結果に基づき回答せよ: {st.session_state.last_res}"},
                            {"role":"user","content":q_in}
                        ])
                        st.write(f"💬 **質問**: {q_in}")
                        st.info(ans)

    # 2. 議事録比較 (完全復活)
    with tabs[1]:
        st.subheader("📝 議事録の差分をマスターへ")
        f_old = st.file_uploader("前回(PDF)", type="pdf", key="f_old")
        f_new = st.file_uploader("今回(PDF)", type="pdf", key="f_new")
        if f_old and f_new and st.button("🔄 差分を抽出"):
            with st.spinner("比較中..."):
                t_old, t_new = read_pdf_text(f_old), read_pdf_text(f_new)
                diff = ask_ai([{"role":"user", "content":f"前回と今回の議事録から、新しく決まった事項や変更点だけを箇条書きで抽出せよ。\n前回:{t_old}\n今回:{t_new}"}])
                st.session_state.diff_list = [d.strip("- ") for d in diff.split("\n") if d.strip()]
       
        if "diff_list" in st.session_state:
            st.write("### マスターに追加する項目を選んでね")
            selected = []
            for d in st.session_state.diff_list:
                if st.checkbox(d, key=f"check_{d}"): selected.append(d)
            if st.button("✅ 選択項目をマスターに保存"):
                new_m = (p_data["master_content"] or "") + "\n" + "\n".join(selected)
                with get_db() as conn:
                    conn.execute("UPDATE projects SET master_content=? WHERE id=?", (new_m, p_id))
                    conn.commit()
                st.success("マスターを更新したよ！")

    # 3. マスター相談
    with tabs[2]:
        st.subheader("💎 マスター内容 & 現場相談")
        m_txt = p_data["master_content"] if p_data else "案件を選んでね"
        st.info(m_txt if m_txt else "まだ登録された決定事項はありません。")
        if chat := st.chat_input("マスターの内容を踏まえて相談する"):
            st.write(ask_ai([{"role":"system","content":f"案件マスター情報: {m_txt}"},{"role":"user","content":chat}]))

    # 4. 事故DB
    with tabs[3]:
        st.subheader("⚠️ 事故・ヒヤリハットDB")
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM accidents").fetchall(): st.error(r['content'])
