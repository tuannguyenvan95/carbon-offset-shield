# CarbonOffsetShield (Autonomous AI ESG Carbon Credit Escrow)

CarbonOffsetShield is an autonomous intelligent escrow contract primitive built on GenLayer that allows Web3 enterprises to safely buy Carbon Credit Certificates (Verra, Gold Standard, etc.) for ESG compliance. 

The contract locks purchase funds in escrow. Once the seller submits a registry page and certificate document, GenLayer's decentralized AI consensus (using LLM validator nodes) subjectively checks that the credits are permanently retired ("RETIRED"), match the specified beneficiary name, and equal or exceed the minimum requested tonnage. If verified, the contract payouts the seller; if rejected, it refunds the buyer.

---

## Why this contract primitive requires GenLayer (One-Line Pitch)
Traditional smart contracts cannot read external web pages or evaluate natural language certification documents on-chain to prevent double-spending. GenLayer provides direct web access and subjective LLM consensus to evaluate semantic certificate meanings on-chain.

---

## Deployment Information
* **Network:** studionet (GenLayer Studio Network)
* **Contract Address:** 0xE9Be93310b0Eb4e2fBBb45FaF5164794dFFD59a5

---

## How Consensus & Custom Semantic Validation Works

Traditional blockchain consensus requires identical byte-for-byte execution results. In GenLayer, subjective execution is handled through decentralized AI consensus (Optimistic Democracy) utilizing the Equivalence Principle:

1. **Leader Node Adjudication:**
   The leader node executes the `submit_and_verify` transaction by running `leader_fn()`. This fetches the web page contents, calls the LLM twice with a strict evaluation prompt (to prevent jailbreaks and hallucinations), averages the confidence scores, and outputs a JSON verdict.
2. **Validator Consensus Verification:**
   Validator nodes execute `validator_fn()`. Instead of comparing the raw JSON string or natural language explanation (which differs slightly on every LLM call), the validator checks:
   - That the validator's verdict matches the leader's verdict (e.g. both agree on `VERIFIED_RETIRED`, `REJECTED`, or `ABORT`).
   - That both validator and leader agree on the confidence bucket (both are >= 65% or both are < 65%).
   This aligns consensus on the **meaning and result** of the transaction, rather than its exact syntax, preventing false disagreements.

---

## Illustrative Worked Example

### 1. Initialize Escrow (Buyer deposits funds)
* **Action:** Buyer calls `create_order(seller_addr, beneficiary_name, min_tonnes)` sending `10 GEN` in value.
  - `seller_addr`: `0x70997970C51812dc3A010C7d01b50e0d17dc79C8`
  - `beneficiary_name`: `"Tesla Motors Inc"`
  - `min_tonnes`: `500`
* **Result:** Returns `order_id` `"1"`. Status is set to `OPEN`.

### 2. Submit Verification Proof (Seller)
* **Action:** Seller calls `submit_and_verify(order_id, registry_url, certificate_url)`.
  - `order_id`: `"1"`
  - `registry_url`: `"https://registry.verra.org/app/project/TeslaRetirement2026"`
  - `certificate_url`: `"https://verra.org/certs/tesla-500-retired.pdf"`

### 3. Subjective AI Consensus Execution (Expected Result)
* **Web Rendering Output:**
  - `registry_url` content: `"... Beneficiary: Tesla Motors Inc ... Quantity: 500 tonnes ... Status: Retired ..."`
* **Consensus Verdict Output:**
  ```json
  {
    "verdict": "VERIFIED_RETIRED",
    "confidence": 92,
    "reason": "Official Verra registry page verifies that 500 tonnes of carbon credits have been permanently retired for beneficiary Tesla Motors Inc."
  }
  ```
* **Escrow Resolution:**
  - Order status is set to `SETTLED`.
  - 2% protocol fee (`0.2 GEN`) is paid to the treasury wallet.
  - 98% payout (`9.8 GEN`) is sent to the seller address.

---

## Project Structure
```
carbon0ffSetshield/
├── contracts/
│   └── carbon_shield.py         # Intelligent Contract (v0.2.16)
├── tests/
│   └── test_carbon_shield.py    # Offline Mock Regression Test Suite
├── requirements-dev.txt         # Dev dependencies
└── README.md                    # Project Documentation
```

---

## Local Verification Tests

1. Ensure Python 3 is installed.
2. Initialize virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: .\venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements-dev.txt
   ```
4. Run tests:
   ```bash
   pytest tests/
   ```
