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

# =====================
# 👁️ AI・解析関数
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
st.title("🏗️ 施工管理AIツール (マスター・事故DB強化版)")

with get_db() as conn:
    c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
    cur_co = c_set["common_rule"] if c_set else ""

col1, col2 = st.columns([1, 2])

# --- 左カラム：案件・辞書管理 ---
with col1:
    with st.expander("🏢 共通ルール・辞書"):
        t_sub = st.tabs(["企業ルール", "辞書"])
        with t_sub[0]:
            co_v = st.text_area("全案件共通", cur_co, height=100)
            if st.button("保存"):
                with get_db() as conn:
                    conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
                    conn.commit()
                st.rerun()
        with t_sub[1]:
            with get_db() as conn:
                words = conn.execute("SELECT * FROM dictionary").fetchall()
            for w in words:
                st.write(f"📖 **{w['word']}**: {w['mean']}")
            nw, nm = st.text_input("用語"), st.text_input("意味")
            if st.button("登録"):
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
        p_id = p_opts[st.selectbox("案件を選択", list(p_opts.keys()))]
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
       
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], height=80, key=f"br_{p_id}")
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], height=80, key=f"pr_{p_id}")
        if st.button("案件ルール保存"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
                conn.commit()
            st.success("保存完了")

# --- 右カラム：メイン機能 ---
with col2:
    tabs = st.tabs(["📊 手順書解析", "📝 議事録比較", "💎 マスター登録", "⚠️ 事故DB"])
  
    # 1. 手順書解析
    with tabs[0]:
        st.subheader("🔍 手順書精査")
        txt = st.text_area("手順書を貼り付け", height=200)
        if st.button("🚀 精査実行"):
            sys_p = f"施工管理プロ。共通:{cur_co}\nビル:{p_data['building_rule']}\n案件:{p_data['project_rule']}"
            res = ask_ai([{"role":"system","content":sys_p},{"role":"user","content":txt}])
            st.session_state.last_res = res
           
        if "last_res" in st.session_state:
            st.warning(st.session_state.last_res)
            q_in = st.chat_input("この結果に質問する")
            if q_in:
                st.info(ask_ai([{"role":"system","content":f"解析結果に基づき回答: {st.session_state.last_res}"},{"role":"user","content":q_in}]))

    # 2. 議事録比較
    with tabs[1]:
        st.subheader("📝 議事録の差分抽出")
        f_old = st.file_uploader("前回(PDF)", type="pdf")
        f_new = st.file_uploader("今回(PDF)", type="pdf")
        if f_old and f_new and st.button("🔄 差分を抽出"):
            diff = ask_ai([{"role":"user", "content":f"前回と今回の議事録から新決定事項を抽出せよ。\n前回:{read_pdf_text(f_old)}\n今回:{read_pdf_text(f_new)}"}])
            st.session_state.diff_list = [d.strip("- ") for d in diff.split("\n") if d.strip()]
       
        if "diff_list" in st.session_state:
            sel = [d for d in st.session_state.diff_list if st.checkbox(d, key=f"ch_{d}")]
            if st.button("✅ 選択項目をマスターに保存"):
                new_m = (p_data["master_content"] or "") + "\n" + "\n".join(sel)
                with get_db() as conn:
                    conn.execute("UPDATE projects SET master_content=? WHERE id=?", (new_m, p_id))
                    conn.commit()
                st.success("マスターを更新したよ！")
                st.rerun() # 即時反映

    # 3. マスター登録（手入力追加）
    with tabs[2]:
        st.subheader("💎 案件マスター (決定事項)")
        master_area = st.text_area("現在のマスター内容（直接編集も可能）", p_data["master_content"] if p_data else "", height=300)
        if st.button("マスターを更新保存"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET master_content=? WHERE id=?", (master_area, p_id))
                conn.commit()
            st.success("マスターを手動更新したよ！")
       
        st.markdown("---")
        if chat := st.chat_input("マスターの内容を踏まえて相談"):
            st.write(ask_ai([{"role":"system","content":f"マスター情報: {master_area}"},{"role":"user","content":chat}]))

    # 4. 事故DB (PDF解析対応)
    with tabs[3]:
        st.subheader("⚠️ 事故・ヒヤリハットDB解析")
        acc_mode = st.radio("登録方法", ["PDF/画像から抽出", "テキスト直接入力"], horizontal=True)
       
        if acc_mode == "PDF/画像から抽出":
            acc_f = st.file_uploader("事故報告書などをアップ", type=["pdf", "png", "jpg"])
            if acc_f and st.button("🔎 教訓を抽出して登録"):
                # ここでは簡易的にテキスト抽出（PDF想定）
                raw_acc = read_pdf_text(acc_f) if acc_f.type == "application/pdf" else "画像解析中..."
                lesson = ask_ai([{"role":"user", "content":f"以下の事故報告から、今後気をつけるべき「教訓」を1行で抽出せよ: {raw_acc}"}])
                with get_db() as conn:
                    conn.execute("INSERT INTO accidents (content) VALUES (?)", (lesson,))
                    conn.commit()
                st.rerun()
        else:
            acc_in = st.text_input("事故事例や教訓を直接入力")
            if st.button("事故DBに登録"):
                with get_db() as conn:
                    conn.execute("INSERT INTO accidents (content) VALUES (?)", (acc_in,))
                    conn.commit()
                st.rerun()
       
        st.write("### ☢️ 過去の教訓リスト")
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM accidents ORDER BY id DESC").fetchall():
                st.error(r['content'])
