# Amazon Profit Calculator

This project is an Amazon profit calculator that helps sellers estimate their potential profits for products sold on Amazon Japan.

## Project Structure

```
amazon_profit_calculator/
├── amazon_profit_calculator.py
├── amazon_fee_structure.json
├── config.env
├── config.env.sample
├── requirements.txt
├── setup_environment.sh
└── README.md
```

## Setup

1. Clone the repository:
   ```
   git clone <repository-url>
   cd amazon_profit_calculator
   ```

2. Run the setup script:
   ```
   ./setup_environment.sh
   ```

3. Activate the virtual environment:
   ```
   source venv/bin/activate
   ```

4. Create a `config.env` file based on `config.env.sample` and add your Keepa API key:
   ```
   KEEPA_API_KEY=your_api_key_here
   ```

## Usage

Run the profit calculator:

```
python amazon_profit_calculator.py --input input.json --output output.json
```

Replace `input.json` with your input file containing product information, and `output.json` with your desired output file name.

## Requirements

See `requirements.txt` for a list of Python dependencies.

## License

This project is licensed under the MIT License.