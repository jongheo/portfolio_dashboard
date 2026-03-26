from flask import Flask, render_template, jsonify
import gspread
from google.oauth2.service_account import Credentials
import toml
import os
import yfinance as yf # 실시간 주가 조회를 위한 라이브러리 추가

app = Flask(__name__)

def load_secrets():
    render_secret_path = "/etc/secrets/secrets.toml"
    local_secret_path = os.path.join(".streamlit", "secrets.toml")
    if os.path.exists(render_secret_path):
        return toml.load(render_secret_path)
    return toml.load(local_secret_path)

def get_sheet_data():
    """구글 시트에서 기초 데이터를 읽고, yfinance로 현재가를 가져와 모든 지표를 동적으로 계산합니다."""
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

        # 1. 실시간 현재가(yfinance) 조회 및 평가금액 계산
        for row in records:
            acc = row.get("계좌명", "")
            asset = row.get("종목명", "")
            ticker = str(row.get("종목코드", ""))
            asset_class = row.get("자산군", "성장")
            buy_price = float(row.get("매입단가", 0))
            qty = float(row.get("보유수량", 0))
            
            # 티커 오기재 자동 보정 안전장치
            if ticker == "302480.KS": ticker = "309230.KS"

            current_price = buy_price # 시세 조회가 안 될 경우를 대비한 기본값
            
            # yfinance를 통한 실시간 종가 조회
            if ticker:
                try:
                    stock_data = yf.Ticker(ticker)
                    hist = stock_data.history(period="1d")
                    if not hist.empty:
                        current_price = float(hist['Close'].iloc[-1])
                except Exception as e:
                    print(f"[{ticker}] 시세 조회 실패: {e}")

            eval_amt = current_price * qty
            account_totals[acc] = account_totals.get(acc, 0) + eval_amt
            
            # 계좌별/자산군별 종목 개수 카운트 (목표 비중 분할용)
            if acc not in class_counts: class_counts[acc] = {}
            class_counts[acc][asset_class] = class_counts[acc].get(asset_class, 0) + 1
            
            portfolio.append({
                "account": acc, "asset": asset, "asset_class": asset_class,
                "buyPrice": buy_price, "currentPrice": current_price,
                "value": eval_amt,
                "return": round(((current_price - buy_price) / buy_price) * 100, 2) if buy_price > 0 else 0
            })

        # 2. 현재비중, 목표비중, 리밸런싱 가이드 자동 생성
        formatted_data = []
        
        # 계좌별 자산군 목표 비중 매핑 (포트폴리오 설계 가이드 기준)
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
            
            # 현재 비중(%) 계산
            cur_weight = round((p["value"] / total_val) * 100, 1)
            
            # 목표 비중(%) 계산 (해당 자산군의 목표 비중을 종목 수만큼 1/N 배분)
            class_target_weight = target_weight_map.get(acc, {}).get(a_class, 0)
            asset_count = class_counts.get(acc, {}).get(a_class, 1)
            target_weight = round(class_target_weight / asset_count, 1) if asset_count > 0 else 0
            
            # 가이드 액션 산출 (현재 비중과 목표 비중의 오차가 2% 이상 날 경우 알림)
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
    """사용자 접속 시 index.html 화면을 렌더링합니다."""
    return render_template('index.html')

@app.route('/api/portfolio')
def get_portfolio():
    """프론트엔드에서 데이터를 요청할 때 구글 시트 데이터를 JSON으로 반환합니다."""
    data = get_sheet_data()
    return jsonify(data)

if __name__ == '__main__':
    app.run(debug=True, port=5000)