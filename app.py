import streamlit as st
import sqlite3
import os
from openai import OpenAI

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

# DB初期化
with get_db() as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, project_name TEXT, building_name TEXT, building_rule TEXT DEFAULT '', project_rule TEXT DEFAULT '', master_content TEXT DEFAULT '', UNIQUE(project_name, building_name))")
    conn.execute("CREATE TABLE IF NOT EXISTS company_settings (id INTEGER PRIMARY KEY, common_rule TEXT DEFAULT '')")
    conn.execute("INSERT OR IGNORE INTO company_settings (id, common_rule) VALUES (1, '')")
    conn.execute("CREATE TABLE IF NOT EXISTS accidents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS dictionary (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT, mean TEXT)")
    conn.commit()

# =====================
# 👁️ AI解析関数
# =====================
def ask_ai_reliable(system_prompt, user_input):
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ],
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ エラー: {str(e)}"

# =====================
# UI構成
# =====================
st.title("🏗️ 施工管理AIツール (ルール直結版)")

# 共通ルールの取得
with get_db() as conn:
    c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
    cur_co = c_set["common_rule"] if c_set else ""

col1, col2 = st.columns([1, 2])

# --- 左カラム：案件とルールの設定 ---
with col1:
    with st.expander("🏢 共通ルール設定"):
        # セッション状態を使って入力を保持
        co_rule_text = st.text_area("全案件共通ルール", cur_co, height=100, key="current_common_rule")
        if st.button("共通ルールをDB保存"):
            with get_db() as conn:
                conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_rule_text,))
                conn.commit()
            st.success("保存完了！")

    st.subheader("🆕 案件管理")
    with get_db() as conn:
        all_p = conn.execute("SELECT * FROM projects").fetchall()
   
    if all_p:
        p_opts = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        selected_label = st.selectbox("案件を選択", list(p_opts.keys()), key="main_p_select")
        p_id = p_opts[selected_label]
       
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
       
        # ⚠️ ここが重要：テキストエリアの値を直接変数に受ける
        b_rule_text = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], height=80, key="current_b_rule")
        p_rule_text = st.text_area("🚧 案件固有ルール", p_data["project_rule"], height=80, key="current_p_rule")
       
        if st.button("案件ルールをDB保存"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (b_rule_text, p_rule_text, p_id))
                conn.commit()
            st.success("案件ルールを保存したよ！")
   
    with st.expander("➕ 新案件登録"):
        nb, np = st.text_input("ビル名"), st.text_input("案件名")
        if st.button("新規登録"):
            if nb and np:
                with get_db() as conn:
                    conn.execute("INSERT OR IGNORE INTO projects (building_name, project_name) VALUES (?, ?)", (nb, np))
                    conn.commit()
                st.rerun()

# --- 右カラム：解析機能 ---
with col2:
    tab_main = st.tabs(["📊 手順書解析", "💬 相談", "⚠️ 事故DB"])
   
    with tab_main[0]:
        st.subheader("🔍 手順書チェック")
       
        # 🚀 修正ポイント：DBからではなく「今画面に入力されている文字」を直接使う
        final_rules = f"""
        【共通ルール】: {st.session_state.get('current_common_rule', cur_co)}
        【ビル固有ルール】: {st.session_state.get('current_b_rule', '')}
        【案件固有ルール】: {st.session_state.get('current_p_rule', '')}
        """
       
        sys_prompt = f"あなたはベテラン施工管理技士です。以下のルールを厳守して手順書を精査してください。\n{final_rules}"

        input_text = st.text_area("手順書を貼り付けてね", height=300, key="input_area")
       
        if st.button("🚀 この内容を精査する"):
            if input_text.strip():
                with st.spinner("AIが現在のルールと照らし合わせています..."):
                    # 直接変数を渡すことで紐付けを確定させる
                    result = ask_ai_reliable(sys_prompt, input_text)
                    st.markdown("---")
                    st.markdown("### 📋 精査結果")
                    st.info(result)
            else:
                st.warning("テキストを入力してね")

    with tab_main[1]:
        st.subheader("💬 AI相談")
        if prompt := st.chat_input("相談してね"):
            st.write(ask_ai_reliable("現場監督として回答して", prompt))

    with tab_main[2]:
        st.subheader("⚠️ 事故DB")
        with get_db() as conn:
            for acc in conn.execute("SELECT * FROM accidents").fetchall():
                st.error(acc['content'])
