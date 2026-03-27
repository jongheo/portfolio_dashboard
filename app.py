from flask import Flask, render_template, jsonify, request
import gspread
from google.oauth2.service_account import Credentials
import toml
import os
import json
from datetime import datetime
import google.generativeai as genai
from supabase import create_client, Client

app = Flask(__name__)

def load_secrets():
    render_secret_path = "/etc/secrets/secrets.toml"
    local_secret_path = os.path.join(".streamlit", "secrets.toml")
    if os.path.exists(render_secret_path): return toml.load(render_secret_path)
    return toml.load(local_secret_path)

def get_supabase():
    secrets = load_secrets()
    return create_client(secrets["supabase"]["url"], secrets["supabase"]["key"])

def get_sheet_data():
    try:
        secrets = load_secrets()
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        credentials = Credentials.from_service_account_info(secrets["gcp_service_account"], scopes=scopes)
        client = gspread.authorize(credentials)
        
        spreadsheet = client.open("자산관리시트_250301")
        balance_sheet = spreadsheet.worksheet("잔고현황")
        records = balance_sheet.get_all_records()
        
        market_sheet = spreadsheet.worksheet("시황기록")
        market_records = market_sheet.get_all_records()
        latest_m = market_records[-1] if market_records else {}

        portfolio = []
        for row in records:
            if not row.get("계좌명"): continue
            def cn(v): return float(str(v).replace(',','').replace('%','')) if v else 0
            buy_p = cn(row.get("매입단가"))
            curr_p = cn(row.get("현재가"))
            portfolio.append({
                "account": row.get("계좌명"),
                "asset": row.get("종목명"),
                "quantity": cn(row.get("보유수량")),
                "buyPrice": buy_p,
                "currentPrice": curr_p,
                "return": round(((curr_p / buy_p) - 1) * 100, 2) if buy_p > 0 else 0,
                "value": cn(row.get("평가금액"))
            })

        return {
            "portfolio": portfolio,
            "market": {
                "date": latest_m.get("날짜", "-"),
                "dow": latest_m.get("다우지수", "-"),
                "snp": latest_m.get("S&P500", "-"),
                "nasdaq": latest_m.get("나스닥", "-"),
                "russell": latest_m.get("Russell2000", "-"), # 추가
                "us10y": latest_m.get("10년물 금리", "-"),
                "wti": latest_m.get("WTI 유가", "-"),
                "gold": latest_m.get("금", "-"),
                "usd": latest_m.get("원달러환율", "-")
            }
        }
    except Exception as e:
        return None

def get_gemini_analysis(data):
    secrets = load_secrets()
    genai.configure(api_key=secrets["gemini"]["api_key"])
    model = genai.GenerativeModel('models/gemini-pro-latest')
    prompt = f"""포트폴리오 분석 리포트를 JSON으로 작성하세요. 데이터: {json.dumps(data, ensure_ascii=False)}"""
    response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
    return json.loads(response.text.strip())

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/portfolio')
def get_portfolio(): return jsonify(get_sheet_data())

@app.route('/api/generate_daily_report', methods=['POST'])
def generate_daily_report():
    data = get_sheet_data()
    analysis = get_gemini_analysis(data)
    today = datetime.now().strftime("%Y-%m-%d")
    get_supabase().table("daily_reports").upsert({"date": today, **analysis}).execute()
    return jsonify({"status": "success", "date": today})

@app.route('/api/get_report_dates')
def get_report_dates():
    res = get_supabase().table("daily_reports").select("date").order("date", desc=True).execute()
    return jsonify([item['date'] for item in res.data])

@app.route('/api/get_report/<date>')
def get_report(date):
    res = get_supabase().table("daily_reports").select("*").eq("date", date).execute()
    return jsonify(res.data[0] if res.data else {})

if __name__ == '__main__':
    app.run(debug=True)