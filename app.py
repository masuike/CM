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
# 🗄️ データベース管理（自動作成機能付き）
# =====================
DB_PATH = "construction_ai.db"

def init_db():
   """データベースとテーブルがなければ自動で作る（エラー防止）"""
   with sqlite3.connect(DB_PATH) as conn:
       conn.execute("CREATE TABLE IF NOT EXISTS company_settings (id INTEGER PRIMARY KEY, common_rule TEXT)")
       conn.execute("CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, building_name TEXT, project_name TEXT, building_rule TEXT, project_rule TEXT, master_content TEXT)")
       conn.execute("CREATE TABLE IF NOT EXISTS dictionary (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT, mean TEXT)")
       conn.execute("CREATE TABLE IF NOT EXISTS accidents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)")
       res = conn.execute("SELECT count(*) FROM company_settings").fetchone()
       if res[0] == 0:
           conn.execute("INSERT INTO company_settings (id, common_rule) VALUES (1, '（ここに共通ルールを入力）')")
       conn.commit()

init_db()

def get_db():
  conn = sqlite3.connect(DB_PATH, check_same_thread=False)
  conn.row_factory = sqlite3.Row
  return conn

# =====================
# 👁️ AI・解析関数 (プロンプト強化版)
# =====================
def read_pdf_text(f):
  r = PyPDF2.PdfReader(f)
  return "\n".join([p.extract_text() for p in r.pages])

def analyze_vision(image, sys_p):
  buffered = BytesIO()
  image.save(buffered, format="PNG")
  img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')

  detailed_prompt = """
  あなたはベテランの施工管理技士として、提出された手順書を厳しく査読してください。
  【共通ルール】【ビルルール】【案件ルール】をすべて踏まえ、以下の視点で具体的に指摘してください。

  1. ルール違反：単独作業（確認者と作業員の兼務）、休日ルールの矛盾、実負荷試験の有無など。
  2. 安全管理：KY、養生、絶縁工具、立ち入り禁止措置、火気使用の記述。
  3. 体制：現場代理人、確認者、作業員の役割分担。
  4. 手順不備：停電・復電手順、異常時の連絡先、緊急連絡網。

  「重大な違反」「改善のヒント」に分けて、なぜダメなのか、どう修正すべきかまで詳しく書いてください。
  """

  res = client.chat.completions.create(
      model="gpt-4o",
      messages=[{"role": "system", "content": sys_p},
                {"role": "user", "content": [{"type":"text","text":detailed_prompt},{"type":"image_url","image_url":{"url":f"data:image/png;base64,{img_str}"}}]}],
      temperature=0.0
  )
  return res.choices[0].message.content

def ask_ai(messages):
  try:
      if messages[0]["role"] == "system":
          messages[0]["content"] += "\n指摘は非常に細かく、重箱の隅をつつくようなベテラン監督の視点で行ってください。"
      res = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.1)
      return res.choices[0].message.content
  except Exception as e:
      return f"エラー: {e}"

# =====================
# UI構成
# =====================
st.title("🏗️ 施工管理AIツール (解析強化＆データ復元版)")

with get_db() as conn:
  c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
  cur_co = c_set["common_rule"] if c_set else ""

col1, col2 = st.columns([1, 2])

# --- 左カラム：案件・辞書・【データ保存/復元】 ---
with col1:
  with st.expander("💾 データのバックアップ・復元"):
      if os.path.exists(DB_PATH):
          with open(DB_PATH, "rb") as f:
              st.download_button(label="📥 今のデータをPCに保存", data=f, file_name="construction_ai_backup.db", mime="application/octet-stream")
      st.markdown("---")
      up_db = st.file_uploader("📤 保存したファイルを読み込む", type=["db"])
      if up_db is not None and st.button("🔄 データを復元する"):
          with open(DB_PATH, "wb") as f:
              f.write(up_db.getbuffer())
          st.success("復元完了！再起動します...")
          st.rerun()

  with st.expander("🏢 共通ルール・辞書"):
      t_sub = st.tabs(["企業ルール", "辞書"])
      with t_sub[0]:
          co_v = st.text_area("全案件共通", cur_co, height=100)
          if st.button("保存", key="co_save"):
              with get_db() as conn:
                  conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
                  conn.commit()
              st.rerun()
      with t_sub[1]:
          with get_db() as conn:
              words = conn.execute("SELECT * FROM dictionary").fetchall()
          for w in words: st.write(f"📖 **{w['word']}**: {w['mean']}")
          nw, nm = st.text_input("用語"), st.text_input("意味")
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
      p_id = p_opts[st.selectbox("案件を選択", list(p_opts.keys()), key="p_select_main")]
      with get_db() as conn:
          p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()

      br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"] or "", height=80, key=f"br_{p_id}")
      pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"] or "", height=80, key=f"pr_{p_id}")
      if st.button("案件ルール保存"):
          with get_db() as conn:
              conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
              conn.commit()
          st.success("保存完了")

  with st.expander("➕ 新規案件追加"):
      nb, np = st.text_input("ビル名"), st.text_input("案件名")
      if st.button("案件登録"):
          with get_db() as conn:
              conn.execute("INSERT OR IGNORE INTO projects (building_name, project_name) VALUES (?, ?)", (nb, np))
              conn.commit()
          st.rerun()

# --- 右カラム：メイン機能 ---
with col2:
  tabs = st.tabs(["📊 手順書解析", "📝 議事録比較", "💎 マスター登録", "⚠️ 事故DB"])

  with tabs[0]:
      st.subheader("🔍 手順書精査")
      m_mode = st.radio("方法", ["PDF/画像アップロード", "テキスト貼り付け"], horizontal=True, key="check_mode")
      sys_p = f"施工管理プロ。共通:{cur_co}\nビル:{p_data['building_rule'] if p_data else ''}\n案件:{p_data['project_rule'] if p_data else ''}"

      if m_mode == "PDF/画像アップロード":
          up_f = st.file_uploader("手順書ファイルをアップ", type=["pdf", "png", "jpg"], key="up_check")
          if up_f and st.button("🚀 ファイルを精査"):
              with st.spinner("AI監督が精密精査中..."):
                  target_img = convert_from_bytes(up_f.read())[0] if up_f.type == "application/pdf" else Image.open(up_f)
                  st.image(target_img, use_container_width=True)
                  st.session_state.last_res = analyze_vision(target_img, sys_p)
      else:
          txt_in = st.text_area("テキストを貼り付け", height=200, key=f"txt_{p_id if p_data else '0'}")
          if st.button("🚀 テキストを精査"):
              with st.spinner("精査中..."):
                  st.session_state.last_res = ask_ai([{"role":"system","content":sys_p},{"role":"user","content":txt_in}])

      if "last_res" in st.session_state:
          st.warning(st.session_state.last_res)
          q_in = st.chat_input("この結果に質問する...")
          if q_in:
              st.info(ask_ai([{"role":"system","content":f"解析結果に基づき回答: {st.session_state.last_res}"},{"role":"user","content":q_in}]))

  with tabs[1]:
      st.subheader("📝 議事録の差分抽出")
      f_old = st.file_uploader("前回(PDF)", type="pdf", key="f_old")
      f_new = st.file_uploader("今回(PDF)", type="pdf", key="f_new")
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
              st.rerun()

  with tabs[2]:
      st.subheader("💎 案件マスター")
      master_area = st.text_area("直接編集", p_data["master_content"] if p_data else "", height=300)
      if st.button("マスターを保存"):
          with get_db() as conn:
              conn.execute("UPDATE projects SET master_content=? WHERE id=?", (master_area, p_id))
              conn.commit()
          st.success("保存完了")
      if chat := st.chat_input("相談..."):
          st.write(ask_ai([{"role":"system","content":f"マスター情報: {master_area}"},{"role":"user","content":chat}]))

  with tabs[3]:
      st.subheader("⚠️ 事故・教訓DB")
      acc_mode = st.radio("方式", ["解析", "入力"], horizontal=True)
      if acc_mode == "解析":
          acc_f = st.file_uploader("報告書アップ", type=["pdf", "png", "jpg"], key="acc_up")
          if acc_f and st.button("🔎 教訓抽出"):
              raw = read_pdf_text(acc_f) if acc_f.type=="application/pdf" else "画像から解析"
              lesson = ask_ai([{"role":"user", "content":f"教訓を1行で抽出せよ: {raw}"}])
              with get_db() as conn:
                  conn.execute("INSERT INTO accidents (content) VALUES (?)", (lesson,))
                  conn.commit()
              st.rerun()
      else:
          acc_in = st.text_input("教訓を直接入力")
          if st.button("登録"):
              with get_db() as conn:
                  conn.execute("INSERT INTO accidents (content) VALUES (?)", (acc_in,))
                  conn.commit()
              st.rerun()
     
      st.markdown("---")
      st.write("📋 登録済み教訓 (クリックで詳細表示)")
     
      # 🌟 ここを改善案2（アコーディオン形式）に修正したよ！
      with get_db() as conn:
          # 新しい順に並べて表示
          accidents = conn.execute("SELECT * FROM accidents ORDER BY id DESC").fetchall()
          for r in accidents:
              # 教訓の1行目をタイトルにして、開くと赤いボックスで見えるようにしたよ
              with st.expander(f"💡 {r['content'][:40]}..."):
                  st.error(r['content'])
