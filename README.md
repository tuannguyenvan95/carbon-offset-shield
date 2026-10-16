# CarbonOffsetShield (Autonomous AI ESG Carbon Credit Escrow)

CarbonOffsetShield is an autonomous intelligent escrow contract primitive built on GenLayer that allows Web3 enterprises to safely buy Carbon Credit Certificates (Verra, Gold Standard, ACR, etc.) for ESG compliance. 

The contract locks purchase funds in escrow. Once the seller submits registry and certificate links, GenLayer's decentralized AI consensus (using LLM validator nodes) subjectively checks that the credits are permanently retired ("RETIRED"), match the specified beneficiary name, equal or exceed the minimum requested tonnage, and extract the standard and serial number.

---

## Why this contract primitive requires GenLayer (One-Line Pitch)
Traditional smart contracts cannot read external web pages or evaluate natural language certification documents on-chain to prevent double-spending. GenLayer provides direct web access and subjective LLM consensus to evaluate semantic certificate meanings on-chain.

---

## Deployment Information
* **Network:** studionet (GenLayer Studio Network)
* **Contract Address:** 0x39D17C0592770bf9e7d01B6064E75901334c356a

---

## Core Lifecycle & Settlement State Protections

### 1. Serial Registry & Double-Spending Protection
* **On-Chain Tracking:** The contract records claimed certificate serials in a dedicated mapping: `claimed_serials: TreeMap[str, str]` (mapping `f"{standard}:{serial_number}"` -> `settled_order_id`).
* **Conflict Prevention & Safe In-Flight Outcome:** If a concurrent in-flight order or subsequent order attempts settlement using an already-claimed standard/serial, the contract detects the conflict deterministically:
  - The conflicting order is marked `REFUNDED` with verdict `REJECTED_DUPLICATE_SERIAL`.
  - 100% of the escrow deposit is automatically refunded to the buyer.
  - The seller receives 0 payout, effectively preventing carbon credit double-spending on-chain.

### 2. INSUFFICIENT_DATA Handling & Proof Correction
* If the submitted registry or certificate documents are partially readable but lack key verification details (e.g. truncated serial number, missing volume units, or unclear beneficiary fields), the AI consensus returns **`INSUFFICIENT_DATA`**.
* The order transitions to `INSUFFICIENT_DATA` status, preserving the locked escrow funds and allowing the seller to provide corrected/supplementary URLs before the order deadline.

### 3. Bounded Recovery Path (`claim_timeout_refund`)
* Every order is initialized with a configurable `deadline: bigint` (default: 3 days).
* If an order remains in `OPEN`, `SUBMITTED`, or `INSUFFICIENT_DATA` past its deadline without valid retirement proof, the buyer can call `claim_timeout_refund(order_id)`.
* This immediately transitions the order to `REFUNDED` (verdict: `TIMEOUT_REFUNDED`) and returns 100% of the locked deposit to the buyer.

---

## How Consensus & Custom Semantic Validation Works

Traditional blockchain consensus requires identical byte-for-byte execution results. In GenLayer, subjective execution is handled through decentralized AI consensus (Optimistic Democracy) utilizing the Equivalence Principle:

1. **Leader Node Adjudication:**
   The leader node executes `submit_and_verify` by running `leader_fn()`. This fetches the web page contents, calls the LLM twice with a strict evaluation prompt (to prevent jailbreaks and hallucinations), averages the confidence scores, and outputs a structured JSON verdict with standard and serial number.
2. **Validator Consensus Verification:**
   Validator nodes execute `validator_fn()`. The validator checks:
   - That the validator's verdict matches the leader's verdict (`VERIFIED_RETIRED`, `REJECTED`, `INSUFFICIENT_DATA`, or `ABORT`).
   - That both validator and leader agree on the confidence bucket (both are >= 65% or both are < 65%).
   - For `VERIFIED_RETIRED`, validators must also strictly agree on the extracted certificate serial number.
   This aligns consensus on the **meaning and outcome** of the transaction, rather than formatting differences.

---

## Illustrative Worked Example

### 1. Initialize Escrow (Buyer deposits funds)
* **Action:** Buyer calls `create_order(seller_addr, beneficiary_name, min_tonnes, deadline_duration_seconds=86400)` sending `10 GEN` in value.
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
* **Consensus Verdict Output:**
  ```json
  {
    "verdict": "VERIFIED_RETIRED",
    "standard": "VERRA",
    "serial_number": "VCS-49129-2026",
    "confidence": 92,
    "reason": "Official Verra registry page verifies that 500 tonnes of carbon credits have been permanently retired for beneficiary Tesla Motors Inc."
  }
  ```
* **Escrow Resolution:**
  - Serial `VERRA:VCS-49129-2026` is registered in `claimed_serials`.
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
│   └── test_carbon_shield.py    # Offline Mock Regression Test Suite (14 tests)
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
   gltest tests/
   ```
