import asyncio
import aiohttp
import json
import logging
import os
from typing import Dict, Any, Optional, List
from decimal import Decimal
from aiohttp import ClientSession, TCPConnector
from aiohttp_retry import RetryClient, ExponentialRetry
import sqlite3
from dotenv import load_dotenv
from tqdm import tqdm
import argparse

# Load environment variables
load_dotenv('config.env')

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AsyncKeepaAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.keepa.com/product"

    async def get_product_details(self, session: RetryClient, jan_code: str) -> List[Dict[str, Any]]:
        params = {
            "key": self.api_key,
            "domain": "5",  # 5 は日本のAmazon
            "code": jan_code,
            "stats": "180",
        }

        try:
            async with session.get(self.base_url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                logger.debug(f"Keepa API response for JAN {jan_code}: {json.dumps(data, indent=2)}")
                
                if "products" in data and len(data["products"]) > 0:
                    return [self.extract_product_info(product) for product in data["products"]]
                else:
                    logger.warning(f"No product data found for JAN code: {jan_code}")
                    return []

        except aiohttp.ClientError as e:
            logger.error(f"Error fetching product details for JAN {jan_code}: {e}")
            return []

    def extract_product_info(self, product: Dict[str, Any]) -> Dict[str, Any]:
        current_price = self.get_current_price(product)
        return {
            "title": product.get("title", "N/A"),
            "asin": product.get("asin", "N/A"),
            "brand": product.get("brand", "N/A"),
            "categories": product.get("categoryTree", []),
            "current_price": current_price,
            "rating": product.get("rating", "N/A"),
            "review_count": product.get("reviewCount", "N/A"),
        }

    def get_current_price(self, product: Dict[str, Any]) -> Optional[int]:
        csv = product.get("csv", [])
        if csv and len(csv) > 1 and len(csv[1]) > 0:
            current_price = csv[1][-1]  # 最新の価格データ
            return current_price if current_price > 0 else None
        return None

class AmazonFeeCalculator:
    def __init__(self, fee_structure_file: str):
        with open(fee_structure_file, 'r') as f:
            self.fee_structure = json.load(f)

    def calculate_fees(self, price: Decimal, category: str) -> Decimal:
        logger.debug(f"Calculating fees for category: {category}")
        for cat in self.fee_structure['fee_structure']:
            if cat['category'].lower() in category.lower():
                logger.debug(f"Matched fee structure: {json.dumps(cat, indent=2)}")
                if isinstance(cat['fee_rate'], str):
                    rate = Decimal(cat['fee_rate'].strip('%')) / 100
                    fee = price * rate
                else:
                    fee = self._calculate_tiered_fee(price, cat['fee_rate'])
                
                minimum_fee = Decimal(cat['minimum_fee'].replace('円', '')) if cat['minimum_fee'] != '該当なし' else Decimal('0')
                final_fee = max(fee, minimum_fee)
                logger.debug(f"Calculated fee: {final_fee}")
                return final_fee
        
        # If category not found, use default rate
        logger.debug("No matching category found, using default rate")
        default_rate = Decimal(self.fee_structure['default_fee_rate'].strip('%')) / 100
        default_minimum_fee = Decimal(self.fee_structure['default_minimum_fee'].replace('円', ''))
        final_fee = max(price * default_rate, default_minimum_fee)
        logger.debug(f"Calculated fee (default): {final_fee}")
        return final_fee

    def _calculate_tiered_fee(self, price: Decimal, fee_rates: List[Dict[str, str]]) -> Decimal:
        for rate in fee_rates:
            condition = rate['condition']
            if '円以下' in condition:
                threshold = Decimal(condition.split('円')[0].split('が')[-1].replace(',', ''))
                if price <= threshold:
                    return price * Decimal(rate['rate'].strip('%')) / 100
            elif '円を超える' in condition:
                threshold = Decimal(condition.split('円')[0].split('が')[-1].replace(',', ''))
                if price > threshold:
                    return price * Decimal(rate['rate'].strip('%')) / 100
        raise ValueError("適用可能な手数料率が見つかりません")

class Database:
    def __init__(self, db_file: str):
        self.conn = sqlite3.connect(db_file)
        self.create_table()

    def create_table(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                jan TEXT PRIMARY KEY,
                data TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    def get_product(self, jan: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT data FROM products WHERE jan = ?", (jan,))
        result = cursor.fetchone()
        if result:
            try:
                return json.loads(result[0])
            except json.JSONDecodeError:
                logger.error(f"Failed to decode JSON for JAN {jan}")
                return None
        return None

    def save_product(self, jan: str, data: Dict[str, Any]):
        cursor = self.conn.cursor()
        try:
            json_data = json.dumps(data)
            cursor.execute("INSERT OR REPLACE INTO products (jan, data) VALUES (?, ?)",
                           (jan, json_data))
            self.conn.commit()
        except (sqlite3.Error, json.JSONEncodeError) as e:
            logger.error(f"Failed to save product data for JAN {jan}: {str(e)}")
            self.conn.rollback()

    def close(self):
        self.conn.close()

async def calculate_profit(selling_price: Decimal, purchase_price: Decimal, category: str, fee_calculator: AmazonFeeCalculator) -> Dict[str, Any]:
    fees = fee_calculator.calculate_fees(selling_price, category)
    profit = selling_price - purchase_price - fees
    return {
        "selling_price": selling_price,
        "purchase_price": purchase_price,
        "fees": fees,
        "profit": profit,
        "profit_margin": round((profit / selling_price * 100), 2) if selling_price > 0 else Decimal('0')
    }

async def process_product(session: RetryClient, keepa_api: AsyncKeepaAPI, jan_code: str, purchase_price: Decimal, fee_calculator: AmazonFeeCalculator, db: Database) -> List[Dict[str, Any]]:
    logger.info(f"処理中: JAN {jan_code}")

    # Check cache first
    cached_product = db.get_product(jan_code)
    if cached_product:
        logger.info(f"キャッシュから商品情報を取得: {jan_code}")
        logger.debug(f"Cached product details: {json.dumps(cached_product, indent=2)}")
        product_details = [cached_product]  # リストに変換
    else:
        product_details = await keepa_api.get_product_details(session, jan_code)
        if product_details:
            for product in product_details:
                db.save_product(f"{jan_code}_{product['asin']}", product)  # JANコードとASINの組み合わせでキャッシュ
        logger.debug(f"Fetched product details: {json.dumps(product_details, indent=2)}")

    results = []
    for product in product_details:
        logger.debug(f"Processing product: {product}")
        logger.debug(f"Product type: {type(product)}")
        if isinstance(product, dict):
            logger.info(f"商品情報取得成功: {product.get('title', 'N/A')}")
            
            current_price = product.get("current_price")
            if current_price is not None:
                current_price = Decimal(str(current_price))
                category = product.get("categories", [{}])[0].get("name", "その他")
                profit_info = await calculate_profit(current_price, purchase_price, category, fee_calculator)
                
                logger.info(f"利益計算完了: 利益 {profit_info['profit']}円, 利益率 {profit_info['profit_margin']}%")
                
                results.append({**product, **profit_info})
            else:
                logger.warning(f"ASIN {product.get('asin', 'N/A')}: 現在価格が取得できません")
                results.append({**product, "error": "現在価格が取得できません", "purchase_price": purchase_price})
        else:
            logger.error(f"JAN {jan_code}: 予期しない商品データ形式: {type(product)}")
            results.append({"jan": jan_code, "error": "予期しない商品データ形式", "purchase_price": purchase_price})

    return results

def print_result(result: Dict[str, Any]):
    print("\n============ 商品情報 ============")
    print(f"商品名: {result.get('title', 'N/A')}")
    print(f"ASIN: {result.get('asin', 'N/A')}")
    print(f"ブランド: {result.get('brand', 'N/A')}")
    print(f"カテゴリー: {result['categories'][0]['name'] if result.get('categories') else 'N/A'}")
    print(f"現在価格: {result.get('current_price', 'N/A')}円")
    print(f"評価: {result.get('rating', 'N/A')}")
    print(f"レビュー数: {result.get('review_count', 'N/A')}")
    
    print("\n============ 利益計算結果 ============")
    print(f"販売価格: {result.get('selling_price', 'N/A')}円")
    print(f"仕入価格: {result.get('purchase_price', 'N/A')}円")
    print(f"手数料: {result.get('fees', 'N/A')}円")
    print(f"利益: {result.get('profit', 'N/A')}円")
    print(f"利益率: {result.get('profit_margin', 'N/A')}%")
    
    if "error" in result:
        print(f"\nエラー: {result['error']}")

async def main(products: List[Dict[str, Any]], keepa_api_key: str, fee_structure_file: str, db_file: str):
    keepa_api = AsyncKeepaAPI(keepa_api_key)
    fee_calculator = AmazonFeeCalculator(fee_structure_file)
    db = Database(db_file)

    retry_options = ExponentialRetry(attempts=3)
    connector = TCPConnector(limit=10)  # 同時接続数を制限

    try:
        async with RetryClient(retry_options=retry_options, connector=connector, raise_for_status=True) as session:
            tasks = [process_product(session, keepa_api, product["jan"], Decimal(str(product["purchase_price"])), fee_calculator, db) for product in products]
            results = []
            for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Processing products"):
                try:
                    results.extend(await f)
                except Exception as e:
                    logger.error(f"Error processing product: {str(e)}")
        
        for result in results:
            print_result(result)

        db.close()
        return results
    except Exception as e:
        logger.error(f"An unexpected error occurred: {str(e)}")
        db.close()
        return []

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Amazon Fee Calculator")
    parser.add_argument("--input", required=True, help="Input JSON file containing product information")
    parser.add_argument("--output", default="output.json", help="Output JSON file for results")
    args = parser.parse_args()

    with open(args.input, 'r') as f:
        PRODUCTS = json.load(f)

    KEEPA_API_KEY = os.getenv("KEEPA_API_KEY")
    FEE_STRUCTURE_FILE = "amazon_fee_structure.json"
    DB_FILE = "product_cache.db"

    results = asyncio.run(main(PRODUCTS, KEEPA_API_KEY, FEE_STRUCTURE_FILE, DB_FILE))

    # Save results to output file
    with open(args.output, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"Results saved to {args.output}")