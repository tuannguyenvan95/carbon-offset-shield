# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import json


def _addr_str(addr: Address) -> str:
    try:
        return addr.as_hex
    except Exception:
        return str(addr)


def _parse_llm_json(text) -> dict:
    if isinstance(text, dict):
        return text
    if hasattr(text, "content"):
        text = text.content
    try:
        cleaned = str(text).strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())
    except Exception as e:
        return {"verdict": "ABORT", "confidence": 0, "reason": f"Parse error: {str(e)}", "standard": "OTHER", "serial_number": ""}


def _safe_parse(raw) -> dict:
    data = _parse_llm_json(raw)
    if not isinstance(data, dict):
        return None

    verdict = str(data.get("verdict", "")).strip().upper()
    if verdict not in ("VERIFIED_RETIRED", "REJECTED", "INSUFFICIENT_DATA", "ABORT"):
        return None

    conf = data.get("confidence", 0)
    if isinstance(conf, float):
        conf = int(conf)
    if not isinstance(conf, int) or not (0 <= conf <= 100):
        return None

    reason = str(data.get("reason", ""))
    standard = str(data.get("standard", "OTHER")).strip().upper()
    serial_number = str(data.get("serial_number", "")).strip().upper()

    # Bind confidence directly to verdict: Low confidence falls back to ABORT
    if conf < 65 and verdict != "ABORT":
        verdict = "ABORT"
        reason = f"[low_confidence: {conf}%] " + reason

    return {
        "verdict": verdict,
        "confidence": conf,
        "reason": reason[:300],
        "standard": standard[:50],
        "serial_number": serial_number[:100],
    }


@allow_storage
@dataclass
class CarbonOrder:
    id: str
    buyer: str
    seller: str
    beneficiary_name: str
    min_tonnes: bigint
    registry_url: str
    certificate_url: str
    deposit_amount: bigint
    status: str       # OPEN | SUBMITTED | INSUFFICIENT_DATA | AUDITING | SETTLED | REFUNDED | ESCALATED
    verdict: str      # VERIFIED_RETIRED | REJECTED | INSUFFICIENT_DATA | ABORT | REJECTED_DUPLICATE_SERIAL | TIMEOUT_REFUNDED
    confidence: bigint
    reason: str
    standard: str
    serial_number: str
    deadline: bigint


class Contract(gl.Contract):
    orders: TreeMap[str, CarbonOrder]
    order_counter: bigint
    treasury_address: str
    total_locked_funds: bigint
    claimed_serials: TreeMap[str, str]

    def __init__(self, treasury_addr: str):
        self.order_counter = bigint(0)
        treasury_addr = treasury_addr.strip() if treasury_addr else ""
        if not treasury_addr:
            raise UserError("Treasury address is required")
        try:
            Address(treasury_addr)
        except Exception:
            raise UserError("Invalid treasury address format")
        self.treasury_address = treasury_addr
        self.total_locked_funds = bigint(0)

    def _treasury(self) -> Address:
        if not self.treasury_address:
            raise UserError("Treasury address not configured")
        return Address(self.treasury_address)

    def _is_http(self, url: str) -> bool:
        u = url.strip().lower()
        return u.startswith("http://") or u.startswith("https://")

    def _now(self) -> bigint:
        try:
            return bigint(gl.block.timestamp)
        except Exception:
            try:
                return bigint(gl.message.timestamp)
            except Exception:
                return bigint(0)

    @gl.public.write.payable
    def create_order(
        self,
        seller_addr: str,
        beneficiary_name: str,
        min_tonnes: int,
        deadline_duration_seconds: int = 259200,
    ) -> str:
        """Buyer deposits purchase funds into escrow for verifiable carbon credit retirement."""
        deposit = gl.message.value
        if deposit <= bigint(0):
            raise UserError("Deposit amount must be greater than 0")

        seller_addr = seller_addr.strip()
        beneficiary_name = beneficiary_name.strip()

        if not seller_addr:
            raise UserError("Seller address is required")
        try:
            Address(seller_addr)
        except Exception:
            raise UserError("Invalid seller address format")

        if len(beneficiary_name) < 3:
            raise UserError("Beneficiary name too short")
        if min_tonnes <= 0:
            raise UserError("min_tonnes must be greater than 0")
        if deadline_duration_seconds <= 0:
            raise UserError("deadline_duration_seconds must be greater than 0")

        self.order_counter += bigint(1)
        oid = str(self.order_counter)
        order_deadline = self._now() + bigint(deadline_duration_seconds)

        self.orders[oid] = CarbonOrder(
            id=oid,
            buyer=_addr_str(gl.message.sender_address),
            seller=seller_addr,
            beneficiary_name=beneficiary_name,
            min_tonnes=bigint(min_tonnes),
            registry_url="",
            certificate_url="",
            deposit_amount=deposit,
            status="OPEN",
            verdict="",
            confidence=bigint(0),
            reason="",
            standard="",
            serial_number="",
            deadline=order_deadline,
        )
        self.total_locked_funds += deposit
        return oid

    @gl.public.write
    def cancel_order(self, order_id: str) -> None:
        """Buyer can cancel and refund before seller submits proof of retirement."""
        if order_id not in self.orders:
            raise UserError("Order not found")
        order = self.orders[order_id]

        if _addr_str(gl.message.sender_address).lower() != order.buyer.lower():
            raise UserError("Only buyer can cancel order")
        if order.status != "OPEN":
            raise UserError("Can only cancel OPEN orders")

        order.status = "REFUNDED"
        order.verdict = "ABORT"
        order.reason = "Cancelled by buyer before retirement proof submission"
        self.orders[order_id] = order

        refund_amt = order.deposit_amount
        if self.total_locked_funds >= refund_amt:
            self.total_locked_funds -= refund_amt
        else:
            self.total_locked_funds = bigint(0)

        if refund_amt > bigint(0):
            gl.get_contract_at(Address(order.buyer)).emit_transfer(value=refund_amt)

    @gl.public.write
    def claim_timeout_refund(self, order_id: str) -> None:
        """Bounded recovery path: buyer reclaims escrow if deadline has expired and proof is unfulfilled or insufficient."""
        if order_id not in self.orders:
            raise UserError("Order not found")
        order = self.orders[order_id]

        sender = _addr_str(gl.message.sender_address).lower()
        if sender != order.buyer.lower():
            raise UserError("Only buyer can claim timeout refund")

        if order.status not in ("OPEN", "SUBMITTED", "INSUFFICIENT_DATA", "AUDITING", "ESCALATED"):
            raise UserError(f"Cannot claim timeout refund for order in status {order.status}")

        curr_time = self._now()
        if curr_time < order.deadline:
            raise UserError("Order deadline has not expired yet")

        deposit_amt = order.deposit_amount
        order.status = "REFUNDED"
        order.verdict = "TIMEOUT_REFUNDED"
        order.reason = "Escrow recovered by buyer: deadline expired without valid retirement proof"
        self.orders[order_id] = order

        if self.total_locked_funds >= deposit_amt:
            self.total_locked_funds -= deposit_amt
        else:
            self.total_locked_funds = bigint(0)

        if deposit_amt > bigint(0):
            gl.get_contract_at(Address(order.buyer)).emit_transfer(value=deposit_amt)

    @gl.public.write
    def submit_and_verify(
        self,
        order_id: str,
        registry_url: str,
        certificate_url: str,
    ) -> None:
        """Seller submits/corrects live Registry and Certificate links, triggering autonomous AI consensus verification."""
        if order_id not in self.orders:
            raise UserError("Order not found")
        order = self.orders[order_id]

        # Allowed lifecycle states: initial submission or correction after INSUFFICIENT_DATA / ESCALATED
        if order.status not in ("OPEN", "SUBMITTED", "INSUFFICIENT_DATA", "ESCALATED"):
            raise UserError("Order is not available for verification")

        sender = _addr_str(gl.message.sender_address).lower()
        if sender != order.seller.lower() and sender != order.buyer.lower():
            raise UserError("Only seller or buyer can trigger verification")

        registry_url = registry_url.strip()
        certificate_url = certificate_url.strip() if certificate_url else ""

        if not self._is_http(registry_url):
            raise UserError("registry_url must start with http(s)://")
        if certificate_url and not self._is_http(certificate_url):
            raise UserError("certificate_url must start with http(s)://")

        order.registry_url = registry_url
        order.certificate_url = certificate_url
        order.status = "AUDITING"
        self.orders[order_id] = order

        b_name = str(order.beneficiary_name)
        m_tonnes = str(order.min_tonnes)
        r_url = str(registry_url)
        c_url = str(certificate_url)

        def leader_fn():
            proof_texts = []
            for u in (r_url, c_url):
                if u:
                    try:
                        res = gl.nondet.web.render(u, mode="text")
                        t = res.content if hasattr(res, "content") else str(res)
                        if t and len(t.strip()) >= 20:
                            proof_texts.append(f"Source ({u}):\n{t[:3500]}")
                    except Exception:
                        pass

            if not proof_texts:
                return {
                    "verdict": "ABORT",
                    "confidence": 0,
                    "reason": "All carbon registry fetch calls failed",
                    "standard": "OTHER",
                    "serial_number": "",
                }

            prompt = f"""
SYSTEM: You are a strict Environmental & Carbon Credit Registry Verification Auditor.
Verify whether the submitted official registry/certificate documents prove a permanent carbon credit retirement matching requirements.

REQUIRED BENEFICIARY: {b_name}
REQUIRED MINIMUM TONNES: {m_tonnes}

OFFICIAL REGISTRY & CERTIFICATE PROOFS:
{"\n\n".join(proof_texts)}

Rules:
- VERIFIED_RETIRED (conf >= 75): Status is explicitly "RETIRED" or "CANCELLED" (permanently out of circulation), quantity >= {m_tonnes}, beneficiary matches "{b_name}", and standard/serial are clearly identifiable.
- REJECTED (conf >= 75): Credit is "ACTIVE" (not retired), quantity is insufficient, wrong beneficiary, or fraudulent certificate.
- INSUFFICIENT_DATA (conf >= 70): Document is partially readable but missing critical verification fields (e.g. serial number cannot be located, beneficiary field is truncated/unspecified, or volume units are ambiguous), requiring seller correction.
- ABORT: Registry page is paywalled, 404, rate-limited, captcha-blocked, or unreadable.

OUTPUT ONLY JSON:
{{
  "verdict": "VERIFIED_RETIRED" | "REJECTED" | "INSUFFICIENT_DATA" | "ABORT",
  "standard": "VERRA" | "GOLD_STANDARD" | "ACR" | "CAR" | "OTHER",
  "serial_number": "string serial number or unique batch identifier (e.g. 1234-5678-VCS)",
  "confidence": 0-100,
  "reason": "max 300 chars technical explanation"
}}
"""
            try:
                raw1 = gl.nondet.exec_prompt(prompt, response_format="json")
                raw2 = gl.nondet.exec_prompt(prompt, response_format="json")

                p1 = _safe_parse(raw1)
                p2 = _safe_parse(raw2)

                if p1 is None or p2 is None:
                    return {
                        "verdict": "ABORT",
                        "confidence": 0,
                        "reason": "parse_failed",
                        "standard": "OTHER",
                        "serial_number": "",
                    }

                if p1["verdict"] != p2["verdict"]:
                    return {
                        "verdict": "ABORT",
                        "confidence": 0,
                        "reason": "multi_sample_divergence",
                        "standard": "OTHER",
                        "serial_number": "",
                    }

                p1["confidence"] = (p1["confidence"] + p2["confidence"]) // 2
                return p1
            except Exception as e:
                return {
                    "verdict": "ABORT",
                    "confidence": 0,
                    "reason": f"LLM error: {str(e)}",
                    "standard": "OTHER",
                    "serial_number": "",
                }

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False

            leader_data = leader_res.calldata if hasattr(leader_res, "calldata") else leader_res
            leader = _safe_parse(leader_data)
            if leader is None:
                return False

            mine = _safe_parse(leader_fn())
            if mine is None:
                return False

            # Agree on verdict and confidence threshold bucket
            verdicts_match = (mine["verdict"] == leader["verdict"])
            conf_buckets_match = ((mine["confidence"] >= 65) == (leader["confidence"] >= 65))

            # For verified retirements, ensure agreement on standard and serial
            if verdicts_match and mine["verdict"] == "VERIFIED_RETIRED":
                serials_match = (mine["serial_number"] == leader["serial_number"])
                return verdicts_match and conf_buckets_match and serials_match

            return verdicts_match and conf_buckets_match

        result_raw = gl.vm.run_nondet(leader_fn, validator_fn)
        result = _safe_parse(result_raw)

        if result is None:
            result = {
                "verdict": "ABORT",
                "confidence": 0,
                "reason": "adjudication_failed",
                "standard": "OTHER",
                "serial_number": "",
            }

        verdict = result["verdict"]
        confidence = result["confidence"]
        reason = result["reason"]
        standard = result["standard"]
        serial_number = result["serial_number"]

        # Post-consensus deterministic normalization
        if confidence < 65 and verdict != "ABORT":
            verdict = "ABORT"

        order.verdict = verdict
        order.confidence = bigint(confidence)
        order.reason = reason
        order.standard = standard
        order.serial_number = serial_number

        deposit_amt = order.deposit_amount
        seller_addr = Address(order.seller)
        buyer_addr = Address(order.buyer)

        if verdict == "VERIFIED_RETIRED":
            # Concurrent/double-spending protection: check if serial was already claimed
            serial_key = f"{standard}:{serial_number}" if serial_number else ""
            if serial_key and (serial_key in self.claimed_serials):
                claimed_by = self.claimed_serials[serial_key]
                if claimed_by != order_id:
                    # CONFLICT DETECTED: Serial already used in another settled order!
                    # Safe outcome: Refund 100% of escrow back to buyer, block seller payout
                    order.status = "REFUNDED"
                    order.verdict = "REJECTED_DUPLICATE_SERIAL"
                    order.reason = f"Conflict: Serial {serial_key} already claimed by order #{claimed_by}. Escrow refunded to buyer."
                    if deposit_amt > bigint(0):
                        gl.get_contract_at(buyer_addr).emit_transfer(value=deposit_amt)

                    if self.total_locked_funds >= deposit_amt:
                        self.total_locked_funds -= deposit_amt
                    else:
                        self.total_locked_funds = bigint(0)

                    self.orders[order_id] = order
                    return

            # Register serial as permanently claimed
            if serial_key:
                self.claimed_serials[serial_key] = order_id

            # Successful verification: 2% protocol fee to treasury, 98% to seller
            fee = (deposit_amt * bigint(2)) // bigint(100)
            seller_payout = deposit_amt - fee

            if fee > bigint(0):
                gl.get_contract_at(self._treasury()).emit_transfer(value=fee)
            if seller_payout > bigint(0):
                gl.get_contract_at(seller_addr).emit_transfer(value=seller_payout)

            order.status = "SETTLED"
            if self.total_locked_funds >= deposit_amt:
                self.total_locked_funds -= deposit_amt
            else:
                self.total_locked_funds = bigint(0)

        elif verdict == "REJECTED":
            # Fraudulent or invalid proof: 100% refund back to buyer
            if deposit_amt > bigint(0):
                gl.get_contract_at(buyer_addr).emit_transfer(value=deposit_amt)

            order.status = "REFUNDED"
            if self.total_locked_funds >= deposit_amt:
                self.total_locked_funds -= deposit_amt
            else:
                self.total_locked_funds = bigint(0)

        elif verdict == "INSUFFICIENT_DATA":
            # Incomplete evidence: transition to INSUFFICIENT_DATA so seller can correct/resubmit proof before deadline
            order.status = "INSUFFICIENT_DATA"

        else:
            # ABORT: Network/fetch error, route to ESCALATED for safe retry or authorized manual resolution
            order.status = "ESCALATED"

        self.orders[order_id] = order

    @gl.public.write
    def resolve_escalated_order(
        self,
        order_id: str,
        release_to_seller: bool,
    ) -> None:
        """Authorized completion path for ESCALATED orders by buyer agreement or treasury authority."""
        if order_id not in self.orders:
            raise UserError("Order not found")
        order = self.orders[order_id]

        if order.status != "ESCALATED":
            raise UserError("Order is not in ESCALATED state")

        sender = _addr_str(gl.message.sender_address).lower()
        treasury_addr_str = self.treasury_address.lower() if self.treasury_address else ""

        if sender != order.buyer.lower() and sender != treasury_addr_str:
            raise UserError("Only buyer or treasury authority can resolve escalated order")

        deposit_amt = order.deposit_amount
        seller_addr = Address(order.seller)
        buyer_addr = Address(order.buyer)

        if release_to_seller:
            # Record serial if provided
            serial_key = f"{order.standard}:{order.serial_number}" if (order.standard and order.serial_number) else ""
            if serial_key and (serial_key in self.claimed_serials):
                claimed_by = self.claimed_serials[serial_key]
                if claimed_by != order_id:
                    raise UserError(f"Cannot release: serial {serial_key} already claimed by order #{claimed_by}")
            if serial_key:
                self.claimed_serials[serial_key] = order_id

            fee = (deposit_amt * bigint(2)) // bigint(100)
            seller_payout = deposit_amt - fee

            if fee > bigint(0):
                gl.get_contract_at(self._treasury()).emit_transfer(value=fee)
            if seller_payout > bigint(0):
                gl.get_contract_at(seller_addr).emit_transfer(value=seller_payout)

            order.status = "SETTLED"
            order.verdict = "RESOLVED_MANUALLY_RELEASED"
            order.reason = f"Escalated order settled: Funds released to seller by {sender}"
        else:
            if deposit_amt > bigint(0):
                gl.get_contract_at(buyer_addr).emit_transfer(value=deposit_amt)

            order.status = "REFUNDED"
            order.verdict = "RESOLVED_MANUALLY_REFUNDED"
            order.reason = f"Escalated order settled: Funds refunded to buyer by {sender}"

        if self.total_locked_funds >= deposit_amt:
            self.total_locked_funds -= deposit_amt
        else:
            self.total_locked_funds = bigint(0)

        self.orders[order_id] = order

    @gl.public.view
    def get_order(self, order_id: str) -> str:
        if order_id not in self.orders:
            raise UserError("Order not found")
        o = self.orders[order_id]
        return json.dumps({
            "id": o.id,
            "buyer": o.buyer,
            "seller": o.seller,
            "beneficiary_name": o.beneficiary_name,
            "min_tonnes": str(o.min_tonnes),
            "registry_url": o.registry_url,
            "certificate_url": o.certificate_url,
            "deposit_amount": str(o.deposit_amount),
            "status": o.status,
            "verdict": o.verdict,
            "confidence": str(o.confidence),
            "reason": o.reason,
            "standard": o.standard,
            "serial_number": o.serial_number,
            "deadline": str(o.deadline),
        })

    @gl.public.view
    def get_claimed_serial(self, serial_key: str) -> str:
        """Returns the order_id that permanently settled and claimed the given standard:serial key."""
        k = serial_key.strip().upper()
        if k in self.claimed_serials:
            return self.claimed_serials[k]
        return ""

    @gl.public.view
    def get_order_counter(self) -> int:
        return int(self.order_counter)

    @gl.public.view
    def get_treasury(self) -> str:
        return self.treasury_address

    @gl.public.view
    def get_all_orders(self) -> str:
        """Returns a JSON-serialized list of all orders for indexing and explorer queries."""
        order_list = []
        count = int(self.order_counter)
        for i in range(1, count + 1):
            oid = str(i)
            if oid in self.orders:
                o = self.orders[oid]
                order_list.append({
                    "id": o.id,
                    "buyer": o.buyer,
                    "seller": o.seller,
                    "beneficiary_name": o.beneficiary_name,
                    "min_tonnes": str(o.min_tonnes),
                    "registry_url": o.registry_url,
                    "certificate_url": o.certificate_url,
                    "deposit_amount": str(o.deposit_amount),
                    "status": o.status,
                    "verdict": o.verdict,
                    "confidence": str(o.confidence),
                    "reason": o.reason,
                    "standard": o.standard,
                    "serial_number": o.serial_number,
                    "deadline": str(o.deadline),
                })
        return json.dumps(order_list)
