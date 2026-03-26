from flask import Flask, render_template, jsonify
import gspread
from google.oauth2.service_account import Credentials
import toml
import os
import yfinance as yf
import pandas as pd  # 데이터 처리를 위해 추가

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
        
        # 1. 시트에 있는 모든 종목코드(티커)를 중복 없이 수집
        ticker_set = set()
        for row in records:
            ticker = str(row.get("종목코드", "")).strip()
            if ticker == "302480.KS": ticker = "309230.KS"  # 티커 보정
            if ticker:
                ticker_set.add(ticker)
                
        ticker_list = list(ticker_set)
        price_dict = {}

        # 2. 한 번의 요청으로 전체 종목 시세 일괄 조회 (속도 10배 향상)
        if ticker_list:
            try:
                # progress=False로 콘솔 로그 지저분해지는 것 방지
                downloaded_data = yf.download(ticker_list, period="1d", progress=False)
                
                # 티커가 1개일 때와 여러 개일 때 데이터 형태가 다름을 처리
                if len(ticker_list) == 1:
                    price_dict[ticker_list[0]] = float(downloaded_data['Close'].iloc[-1])
                else:
                    for t in ticker_list:
                        # 데이터가 없는 경우(NaN) 대비
                        val = downloaded_data['Close'][t].iloc[-1]
                        if pd.notna(val):
                            price_dict[t] = float(val)
            except Exception as e:
                print(f"일괄 시세 조회 실패: {e}")

        portfolio = []
        account_totals = {}
        class_counts = {}

        # 3. 데이터 매핑 및 평가금액 계산
        for row in records:
            acc = row.get("계좌명", "")
            asset = row.get("종목명", "")
            ticker = str(row.get("종목코드", "")).strip()
            if ticker == "302480.KS": ticker = "309230.KS"
            
            asset_class = row.get("자산군", "성장")
            buy_price = float(row.get("매입단가", 0))
            qty = float(row.get("보유수량", 0))

            # 조회된 시세가 dict에 있으면 가져오고, 없거나 0이면 매입단가 유지
            current_price = price_dict.get(ticker, buy_price)
            if current_price == 0:
                current_price = buy_price

            eval_amt = current_price * qty
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
        
        # 4. 계좌별 목표 비중 및 가이드 생성 로직
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