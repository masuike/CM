import streamlit as st
import pandas as pd
import sqlite3
import os
import base64
from io import BytesIO
from openai import OpenAI
from PIL import Image
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
       else:
           st.error("パスワードが違います")
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

def init_db():
   with get_db() as conn:
       conn.execute("""
           CREATE TABLE IF NOT EXISTS projects (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               project_name TEXT,
               building_name TEXT,
               building_rule TEXT DEFAULT '',
               project_rule TEXT DEFAULT '',
               master_content TEXT DEFAULT '',
               UNIQUE(project_name, building_name)
           )
       """)
       conn.execute("CREATE TABLE IF NOT EXISTS company_settings (id INTEGER PRIMARY KEY, common_rule TEXT DEFAULT '')")
       conn.execute("INSERT OR IGNORE INTO company_settings (id, common_rule) VALUES (1, '')")
       conn.execute("CREATE TABLE IF NOT EXISTS accidents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)")
       conn.commit()

init_db()

# =====================
# 👁️ 解析・AI関数
# =====================
def analyze_with_vision(image, prompt):
   buffered = BytesIO()
   image.save(buffered, format="PNG")
   img_str = base64.b64encode(buffered.getvalue()).decode()
   res = client.chat.completions.create(
       model="gpt-4o",
       messages=[
           {"role": "system", "content": prompt},
           {"role": "user", "content": [
               {"type": "text", "text": "画像内の表の文字や手順を精密に読み取り、解析してください。"},
               {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_str}"}}
           ]}
       ],
       temperature=0.0
   )
   return res.choices[0].message.content

def ask_ai(messages):
   try:
       res = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.1)
       return res.choices[0].message.content
   except: return "AIエラーが発生しました"

# =====================
# UI構成
# =====================
st.title("🏗️ 施工管理AIツール (全機能復活版)")

col1, col2 = st.columns([1, 2])

# --- 左カラム：設定と管理 ---
with col1:
   with st.expander("🏢 企業共通ルール"):
       with get_db() as conn:
           c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
           current_co_rule = c_set["common_rule"] if c_set else ""
       co_v = st.text_area("全案件共通", current_co_rule, height=80)
       if st.button("共通ルール保存"):
           with get_db() as conn:
               conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
               conn.commit()
           st.success("保存完了")

   st.subheader("🆕 案件管理")
   with st.expander("➕ 新規案件を追加"):
       new_b = st.text_input("ビル名")
       new_p = st.text_input("案件名")
       if st.button("案件を登録"):
           if new_b and new_p:
               with get_db() as conn:
                   conn.execute("INSERT OR IGNORE INTO projects (building_name, project_name) VALUES (?, ?)", (new_b, new_p))
                   conn.commit()
               st.success("登録完了！")
               st.rerun()

   with get_db() as conn:
       all_p = conn.execute("SELECT id, building_name, project_name FROM projects").fetchall()

   p_data = None
   if all_p:
       p_options = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
       sel_label = st.selectbox("案件を選択", list(p_options.keys()))
       p_id = p_options[sel_label]
       with get_db() as conn:
           p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()

       br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], key=f"br_{p_id}", height=100)
       pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], key=f"pr_{p_id}", height=100)
       if st.button("案件ルールを保存"):
           with get_db() as conn:
               conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
               conn.commit()
           st.success("保存完了")

# --- 右カラム：メイン機能 ---
with col2:
   tabs = st.tabs(["📊 手順書解析", "💬 自由相談・要約", "💎 マスター登録", "⚠️ 事故DB"])

   # --- タブ1: 手順書解析 ---
   with tabs[0]:
       st.subheader("🔍 精密手順書チェック")
       mode = st.radio("解析方法", ["PDF/画像アップロード", "テキストを直接貼り付け"], horizontal=True)

       sys_p = f"""あなたはベテラン施工管理技士です。
【重要確認】緊急連絡先、開始・終了時間、分電盤OFF時の負荷側への周知があるか厳しくチェックしてください。
【参照ルール】共通:{current_co_rule} / 案件:{p_data['building_rule'] if p_data else ''}
"""

       if mode == "PDF/画像アップロード":
           up_file = st.file_uploader("ファイルをアップロード", type=["pdf", "png", "jpg", "jpeg"])
           if up_file:
               if up_file.type == "application/pdf":
                   try:
                       images = convert_from_bytes(up_file.read())
                       target_img = images[0]
                   except: st.error("PDFの画像変換に失敗しました。packages.txtを確認してください。"); st.stop()
               else:
                   target_img = Image.open(up_file)

               st.image(target_img, caption="解析対象", use_container_width=True)
               if st.button("🚀 精密画像解析を実行"):
                   with st.status("AIが目視確認中..."):
                       ans = analyze_with_vision(target_img, sys_p)
                       st.session_state.analysis_res = ans
       else:
           manual_text = st.text_area("手順書テキストを貼り付け", height=250)
           if st.button("🚀 テキスト精査を実行"):
               with st.status("AIがテキストを読み取り中..."):
                   ans = ask_ai([{"role":"system", "content":sys_p}, {"role":"user", "content":manual_text}])
                   st.session_state.analysis_res = ans

       if "analysis_res" in st.session_state:
           st.markdown("### 💡 AI解析結果")
           st.markdown(st.session_state.analysis_res)
           if st.button("結果をクリア"):
               del st.session_state.analysis_res
               st.rerun()

   # --- タブ2: 自由相談 ---
   with tabs[1]:
       st.subheader("💬 AI現場監督に相談")
       if "chat_history" not in st.session_state: st.session_state.chat_history = []
       for msg in st.session_state.chat_history:
           with st.chat_message(msg["role"]): st.markdown(msg["content"])

       if prompt := st.chat_input("議事録を貼って『要約して』と言ったり、現場の悩みを相談してね"):
           st.session_state.chat_history.append({"role": "user", "content": prompt})
           with st.chat_message("user"): st.markdown(prompt)
           with st.chat_message("assistant"):
               res = ask_ai([{"role":"system", "content":"現場のプロとして回答してください。"}] + st.session_state.chat_history)
               st.markdown(res)
               st.session_state.chat_history.append({"role": "assistant", "content": res})

   # --- タブ3: マスター登録 ---
   with tabs[2]:
       st.subheader("💎 決定事項の記録")
       if p_data:
           m_val = st.text_area("この案件の決定事項（AIも参照します）", p_data["master_content"], height=300)
           if st.button("マスター内容を保存"):
               with get_db() as conn:
                   conn.execute("UPDATE projects SET master_content=? WHERE id=?", (m_val, p_id))
                   conn.commit()
               st.success("保存しました！")
       else:
           st.write("左で案件を選んでね。")

   # --- タブ4: 事故DB ---
   with tabs[3]:
       st.subheader("⚠️ 過去の事故・ヒヤリハット")
       new_acc = st.text_input("新しい事故事例を追加")
       if st.button("事故DBに登録"):
           if new_acc:
               with get_db() as conn:
                   conn.execute("INSERT INTO accidents (content) VALUES (?)", (new_acc,))
                   conn.commit()
               st.success("登録完了")
               st.rerun()
       with get_db() as conn:
           acc_rows = conn.execute("SELECT * FROM accidents").fetchall()
       for r in acc_rows: st.info(r['content'])
