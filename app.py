from flask import Flask, render_template, jsonify
import gspread
from google.oauth2.service_account import Credentials
import toml
import os

app = Flask(__name__)

def load_secrets():
    render_secret_path = "/etc/secrets/secrets.toml"
    local_secret_path = os.path.join(".streamlit", "secrets.toml")
    if os.path.exists(render_secret_path):
        return toml.load(render_secret_path)
    return toml.load(local_secret_path)

def get_sheet_data():
    try:
        secrets = load_secrets()
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        credentials = Credentials.from_service_account_info(secrets["gcp_service_account"], scopes=scopes)
        client = gspread.authorize(credentials)
        
        sheet = client.open("자산관리시트_250301").worksheet("잔고현황")
        records = sheet.get_all_records()
        
        portfolio = []
        account_totals = {}
        class_counts = {}

        for row in records:
            acc = row.get("계좌명", "")
            if not acc: continue  # 빈 줄은 건너뛰기
            
            asset = row.get("종목명", "")
            asset_class = row.get("자산군", "성장")
            
            # 구글 시트에 콤마(,)가 포함되어 있어도 숫자로 완벽하게 읽어오는 처리
            def clean_num(val):
                try: return float(str(val).replace(',', ''))
                except: return 0.0

            buy_price = clean_num(row.get("매입단가", 0))
            current_price = clean_num(row.get("현재가", 0))
            eval_amt = clean_num(row.get("평가금액", 0))

            # 시트 오류 방지용 기본값
            if current_price == 0: current_price = buy_price
            if eval_amt == 0: eval_amt = current_price * clean_num(row.get("보유수량", 0))

            account_totals[acc] = account_totals.get(acc, 0) + eval_amt
            
            if acc not in class_counts: class_counts[acc] = {}
            class_counts[acc][asset_class] = class_counts[acc].get(asset_class, 0) + 1
            
            portfolio.append({
                "account": acc, "asset": asset, "asset_class": asset_class,
                "buyPrice": buy_price, "currentPrice": current_price,
                "value": eval_amt,
                "return": round(((current_price - buy_price) / buy_price) * 100, 2) if buy_price > 0 else 0
            })

        formatted_data = []
        
        # 계좌별 자산군 목표 비중 세팅
        target_weight_map = {
            "1.일반계좌1": {"성장": 50, "배당/가치": 30, "채권": 20},
            "2.일반계좌2(해외)": {"성장": 70, "배당/가치": 30},
            "3.종합계좌": {"성장": 50, "배당/가치": 50},
            "4.ISA": {"성장": 100, "채권": 0},
            "5.연금저축1": {"성장": 60, "배당/가치": 40},
            "6.연금저축2": {"성장": 100},
            "7.퇴직연금 DC": {"성장": 60, "채권": 40, "배당/가치": 0},
            "8.IRP 1": {"성장": 100},
            "9.IRP 2": {"성장": 100}
        }

        for p in portfolio:
            acc = p["account"]
            a_class = p["asset_class"]
            total_val = account_totals.get(acc, 1)
            
            cur_weight = round((p["value"] / total_val) * 100, 1)
            
            class_target_weight = target_weight_map.get(acc, {}).get(a_class, 0)
            asset_count = class_counts.get(acc, {}).get(a_class, 1)
            target_weight = round(class_target_weight / asset_count, 1) if asset_count > 0 else 0
            
            diff = cur_weight - target_weight
            if diff > 2.0:
                guide, guide_type = "일부 실현", "sell"
            elif diff < -2.0:
                guide, guide_type = "비중 확대", "buy"
            else:
                guide, guide_type = "비중 유지", "hold"

            formatted_data.append({
                "account": acc,
                "asset": p["asset"],
                "buyPrice": p["buyPrice"],
                "currentPrice": round(p["currentPrice"], 2),
                "return": p["return"],
                "value": round(p["value"], 0),
                "curWeight": cur_weight,
                "targetWeight": target_weight,
                "guide": guide,
                "guideType": guide_type
            })
            
        return formatted_data
        
    except Exception as e:
        print(f"시스템 연동 오류: {e}")
        return []

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/portfolio')
def get_portfolio():
    data = get_sheet_data()
    return jsonify(data)

if __name__ == '__main__':
    app.run(debug=True, port=5000)

from supabase import create_client, Client

# Supabase 초기화 함수
def get_supabase():
    secrets = load_secrets()
    return create_client(secrets["supabase"]["url"], secrets["supabase"]["key"])

# 1. 저장된 모든 리포트 날짜 가져오기 (셀렉트 박스용)
@app.route('/api/get_report_dates')
def get_report_dates():
    try:
        supabase = get_supabase()
        response = supabase.table("daily_reports").select("date").order("date", desc=True).execute()
        return jsonify([item['date'] for item in response.data])
    except Exception as e:
        return jsonify([])

# 2. 특정 날짜의 리포트 상세 데이터 가져오기
@app.route('/api/get_report/<date>')
def get_report(date):
    try:
        supabase = get_supabase()
        response = supabase.table("daily_reports").select("*").eq("date", date).execute()
        if response.data:
            return jsonify(response.data[0])
        return jsonify({"status": "not_found"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# 3. [테스트용] 오늘 작성하신 리포트 강제 저장 API
# 브라우저에서 /api/save_sample_report 접속 시 오늘 리포트가 DB에 들어갑니다.
@app.route('/api/save_sample_report')
def save_sample_report():
    try:
        supabase = get_supabase()
        sample_data = {
            "date": "2026-03-26",
            "title": "미 3대 지수 일제히 상승. 국채 금리 하락이 시장 안정 견인",
            "macro": "10년물 국채금리가 4.3% 초반으로 하락하며 투자 심리가 안정되었습니다. 특히 나스닥이 0.77% 상승하며 기술주 중심의 강세가 연출되었습니다.",
            "strategy": "메리츠금융지주 강세 지속 시 비중 축소 후 현대차2우B나 삼성전자 하락 구간 분할 매수 권고.",
            "news": [
                {"t": "버크셔 해서웨이의 AI 베팅", "c": "워런 버핏이 포트폴리오의 20.4%를 AI 주식에 집중하고 있습니다."},
                {"t": "중동 지정학적 리스크", "c": "WTI 가격은 소폭 하락했으나 분쟁 우려가 여전합니다."}
            ]
        }
        supabase.table("daily_reports").upsert(sample_data).execute()
        return "오늘자 리포트가 Supabase DB에 성공적으로 저장되었습니다!"
    except Exception as e:
        return f"저장 실패: {str(e)}"