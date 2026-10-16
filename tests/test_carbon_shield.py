"""
Comprehensive Test Suite for CarbonOffsetShield GenLayer Intelligent Contract.

Verifies:
  1. schema and syntax checks (pragmas, imports)
  2. create_order: validations, status, counter increment, deadline calculation
  3. cancel_order: authorization guards, refund triggers
  4. submit_and_verify (consensus happy path: VERIFIED_RETIRED) -> 2% fee split + serial claim
  5. submit_and_verify (consensus fraud path: REJECTED) -> 100% buyer refund
  6. submit_and_verify (consensus error path: ABORT / Fetch failed) -> ESCALATED
  7. consensus double-sampling jailbreak defense (diverging verdicts -> ABORT)
  8. consensus low-confidence filter (confidence < 65% -> ABORT)
  9. resolve_escalated_order: admin/buyer manual settlement resolution
  10. Transaction rollback verification (guards revert state on UserError)
  11. Concurrent / duplicate serial protection: conflicting order safely refunded to buyer
  12. INSUFFICIENT_DATA lifecycle: evidence correction and resubmission
  13. Bounded recovery path: claim_timeout_refund after deadline expiration
"""

import sys
import types
import json
import pytest

# -- 1. Create Mock GenLayer Module --
genlayer_mock = types.ModuleType("genlayer")

class Address:
    def __init__(self, val: str):
        val_str = str(val).strip().lower()
        if not val_str.startswith("0x"):
            raise ValueError("Address must start with '0x'")
        self.val = val_str
    def __str__(self):
        return self.val
    def __eq__(self, other):
        if isinstance(other, Address):
            return self.val == other.val
        return self.val == str(other).lower().strip()
    def __hash__(self):
        return hash(self.val)
    @property
    def as_hex(self) -> str:
        return self.val

class bigint(int):
    pass

class u256(int):
    pass

class UserError(Exception):
    pass

class TreeMap:
    def __class_getitem__(cls, item):
        return cls
    def __init__(self):
        self.store = {}
    def __setitem__(self, key, value):
        self.store[str(key)] = value
    def __getitem__(self, key):
        return self.store[str(key)]
    def __contains__(self, key):
        return str(key) in self.store
    def items(self):
        return self.store.items()
    def __len__(self):
        return len(self.store)

def allow_storage(cls):
    return cls

# Mock global gl instance
class MockMessage:
    def __init__(self):
        self.sender_address = Address("0xdefault_sender")
        self.value = bigint(0)
        self.timestamp = bigint(1700000000)

class MockBlock:
    def __init__(self):
        self.timestamp = bigint(1700000000)

class MockWeb:
    def __init__(self):
        self.render_result = "Registry content: RETIRED"
    def render(self, url, mode="text"):
        if "fail" in url:
            raise Exception("Network issue")
        return self.render_result

class MockNondet:
    def __init__(self):
        self.web = MockWeb()
        self._prompt_results = []
        self.call_count = 0
    @property
    def prompt_results(self):
        return self._prompt_results
    @prompt_results.setter
    def prompt_results(self, val):
        self._prompt_results = val
        self.call_count = 0
    def exec_prompt(self, prompt, response_format="json"):
        if self._prompt_results:
            res = self._prompt_results[self.call_count % len(self._prompt_results)]
            self.call_count += 1
            return res
        return {
            "verdict": "VERIFIED_RETIRED",
            "confidence": 90,
            "reason": "Successful mock audit",
            "standard": "VERRA",
            "serial_number": "1234-5678-VCS",
        }

class MockContractInstance:
    def __init__(self, address):
        self.address = address
        self.transfers = []
    def emit_transfer(self, value):
        self.transfers.append(value)

class MockWrite:
    def __call__(self, fn): return fn
    def payable(self, fn): return fn

class MockPublic:
    def __init__(self):
        self.write = MockWrite()
        self.view = lambda fn: fn

class ContractBase:
    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        annotations = getattr(cls, "__annotations__", {})
        for name, type_hint in annotations.items():
            if type_hint is TreeMap or (isinstance(type_hint, type) and type_hint.__name__ == "TreeMap"):
                setattr(instance, name, TreeMap())
        return instance

class MockReturn:
    def __init__(self, calldata):
        self.calldata = calldata

class MockGL:
    def __init__(self):
        self.Contract = ContractBase
        self.public = MockPublic()
        self.message = MockMessage()
        self.block = MockBlock()
        self.nondet = MockNondet()
        self.vm = self
        self.Return = MockReturn
        self.contracts = {}

    def get_contract_at(self, address):
        addr_str = str(address).lower()
        if addr_str not in self.contracts:
            self.contracts[addr_str] = MockContractInstance(address)
        return self.contracts[addr_str]

    def run_nondet(self, leader_fn, validator_fn):
        res = leader_fn()
        ret = MockReturn(calldata=res)
        assert validator_fn(ret) is True
        return res

gl_inst = MockGL()
genlayer_mock.gl = gl_inst
genlayer_mock.Address = Address
genlayer_mock.bigint = bigint
genlayer_mock.u256 = u256
genlayer_mock.UserError = UserError
genlayer_mock.TreeMap = TreeMap
genlayer_mock.allow_storage = allow_storage

sys.modules["genlayer"] = genlayer_mock

# -- 2. Load the Contract Module --
with open("contracts/carbon_shield.py", "r", encoding="utf-8") as f:
    contract_code = f.read()

contract_ns = {
    "gl": gl_inst,
    "Address": Address,
    "bigint": bigint,
    "u256": u256,
    "UserError": UserError,
    "TreeMap": TreeMap,
    "allow_storage": allow_storage,
    "__name__": "contracts.carbon_shield"
}
exec(contract_code, contract_ns)

ContractClass = contract_ns["Contract"]
CarbonOrderClass = contract_ns["CarbonOrder"]

# Clone helper to test rollbacks
def clone_orders(orders: TreeMap) -> TreeMap:
    cloned = TreeMap()
    for k, v in orders.store.items():
        cloned[k] = CarbonOrderClass(
            id=v.id,
            buyer=v.buyer,
            seller=v.seller,
            beneficiary_name=v.beneficiary_name,
            min_tonnes=v.min_tonnes,
            registry_url=v.registry_url,
            certificate_url=v.certificate_url,
            deposit_amount=v.deposit_amount,
            status=v.status,
            verdict=v.verdict,
            confidence=v.confidence,
            reason=v.reason,
            standard=v.standard,
            serial_number=v.serial_number,
            deadline=v.deadline,
        )
    return cloned

# -- 3. Test Cases --

def test_contract_schema_and_integrity():
    """Rule compliance checks: headers, version, imports, structures."""
    assert "# v0.2.16" in contract_code
    assert 'Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6' in contract_code
    assert "from genlayer import *" in contract_code
    assert "VERIFIED_RETIRED" in contract_code
    assert "REJECTED" in contract_code
    assert "INSUFFICIENT_DATA" in contract_code
    assert "ESCALATED" in contract_code
    assert "claimed_serials" in contract_code
    assert "claim_timeout_refund" in contract_code
    assert "class Contract(gl.Contract):" in contract_code


def test_init_validations():
    """Verify __init__ enforces valid treasury configurations and does not reassign TreeMap."""
    c = ContractClass("0xTreasuryAddress")
    assert c.treasury_address == "0xTreasuryAddress"
    assert len(c.orders) == 0
    assert len(c.claimed_serials) == 0
    
    with pytest.raises(UserError, match="Treasury address is required"):
        ContractClass("")
        
    with pytest.raises(UserError, match="Invalid treasury address format"):
        ContractClass("not-a-valid-address")


def test_create_order_happy_path_and_validations():
    """Verify order creation state updates, deadline calculation, and validation rules."""
    c = ContractClass("0xTreasury")
    gl_inst.block.timestamp = bigint(1700000000)
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(500)
    oid = c.create_order(
        seller_addr="0xSeller",
        beneficiary_name="Tesla Motors Inc",
        min_tonnes=1000,
        deadline_duration_seconds=86400
    )
    
    assert oid == "1"
    assert c.order_counter == 1
    assert c.total_locked_funds == 500
    
    order = c.orders["1"]
    assert order.buyer == "0xbuyer"
    assert order.seller == "0xSeller"
    assert order.beneficiary_name == "Tesla Motors Inc"
    assert order.min_tonnes == 1000
    assert order.deposit_amount == 500
    assert order.status == "OPEN"
    assert order.deadline == 1700000000 + 86400
    
    gl_inst.message.value = bigint(0)
    with pytest.raises(UserError, match="Deposit amount must be greater than 0"):
        c.create_order("0xSeller", "Tesla", 10)
        
    gl_inst.message.value = bigint(200)
    with pytest.raises(UserError, match="Seller address is required"):
        c.create_order("", "Tesla", 10)
        
    with pytest.raises(UserError, match="Invalid seller address format"):
        c.create_order("bad-seller-addr", "Tesla", 10)
        
    with pytest.raises(UserError, match="Beneficiary name too short"):
        c.create_order("0xSeller", "Te", 10)
        
    with pytest.raises(UserError, match="min_tonnes must be greater than 0"):
        c.create_order("0xSeller", "Tesla", 0)

    with pytest.raises(UserError, match="deadline_duration_seconds must be greater than 0"):
        c.create_order("0xSeller", "Tesla", 10, deadline_duration_seconds=0)


def test_cancel_order_payout_and_guards():
    """Verify cancellation refunds buyer, updates status, and respects role guards."""
    c = ContractClass("0xTreasury")
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(500)
    oid = c.create_order("0xSeller", "Tesla Motors Inc", 1000)
    gl_inst.contracts.clear()
    
    gl_inst.message.sender_address = Address("0xHacker")
    with pytest.raises(UserError, match="Only buyer can cancel order"):
        c.cancel_order(oid)
        
    gl_inst.message.sender_address = Address("0xBuyer")
    c.cancel_order(oid)
    
    order = c.orders[oid]
    assert order.status == "REFUNDED"
    assert order.verdict == "ABORT"
    assert c.total_locked_funds == 0
    
    buyer_mock = gl_inst.get_contract_at(Address("0xBuyer"))
    assert 500 in buyer_mock.transfers
    
    with pytest.raises(UserError, match="Can only cancel OPEN orders"):
        c.cancel_order(oid)


def test_consensus_verified_retired_payout_split():
    """Verify VERIFIED_RETIRED consensus settles payout (2% fee, 98% seller) and records serial."""
    c = ContractClass("0xTreasury")
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(10000)
    oid = c.create_order("0xSeller", "Tesla Motors Inc", 500)
    
    gl_inst.nondet.web.render_result = "Verra Certificate #49129\nBeneficiary: Tesla Motors Inc\nQuantity: 500 Tonnes\nStatus: RETIRED\nSerial: VCS-49129-2026"
    gl_inst.nondet.prompt_results = [
        {"verdict": "VERIFIED_RETIRED", "standard": "VERRA", "serial_number": "VCS-49129-2026", "confidence": 90, "reason": "Verra document verifies 500 tonnes retired for Tesla Motors Inc"},
        {"verdict": "VERIFIED_RETIRED", "standard": "VERRA", "serial_number": "VCS-49129-2026", "confidence": 94, "reason": "Verra document verifies 500 tonnes retired for Tesla Motors Inc"}
    ]
    gl_inst.contracts.clear()
    
    gl_inst.message.sender_address = Address("0xSeller")
    c.submit_and_verify(oid, "https://verra.org/registry/123", "https://verra.org/cert/123")
    
    order = c.orders[oid]
    assert order.status == "SETTLED"
    assert order.verdict == "VERIFIED_RETIRED"
    assert order.confidence == 92
    assert order.standard == "VERRA"
    assert order.serial_number == "VCS-49129-2026"
    assert c.total_locked_funds == 0
    
    # Check that serial is permanently claimed
    assert c.get_claimed_serial("VERRA:VCS-49129-2026") == oid
    
    treasury_mock = gl_inst.get_contract_at(Address("0xTreasury"))
    seller_mock = gl_inst.get_contract_at(Address("0xSeller"))
    assert 200 in treasury_mock.transfers
    assert 9800 in seller_mock.transfers


def test_duplicate_serial_prevention_safely_refunds_conflicting_order():
    """Verify that concurrent or subsequent orders for the same standard/serial cannot reach payout and safely refund the buyer."""
    c = ContractClass("0xTreasury")
    
    # 1. Order 1 settles and claims serial VCS-DUPLICATE-001
    gl_inst.message.sender_address = Address("0xBuyer1")
    gl_inst.message.value = bigint(5000)
    oid_1 = c.create_order("0xSeller1", "Tesla Motors Inc", 200)
    
    gl_inst.nondet.web.render_result = "Verra Certificate #777\nBeneficiary: Tesla Motors Inc\nQuantity: 200 Tonnes\nStatus: RETIRED"
    gl_inst.nondet.prompt_results = [
        {"verdict": "VERIFIED_RETIRED", "standard": "VERRA", "serial_number": "VCS-DUPLICATE-001", "confidence": 92, "reason": "Valid retirement"}
    ]
    gl_inst.message.sender_address = Address("0xSeller1")
    c.submit_and_verify(oid_1, "https://verra.org/reg/1", "")
    
    assert c.orders[oid_1].status == "SETTLED"
    assert c.get_claimed_serial("VERRA:VCS-DUPLICATE-001") == oid_1
    
    # 2. Order 2 (conflicting in-flight / subsequent order) attempts payout with the same serial
    gl_inst.message.sender_address = Address("0xBuyer2")
    gl_inst.message.value = bigint(6000)
    oid_2 = c.create_order("0xSeller2", "SpaceX Corp", 200)
    assert c.total_locked_funds == 6000
    
    gl_inst.contracts.clear()
    gl_inst.message.sender_address = Address("0xSeller2")
    # AI returns VERIFIED_RETIRED for the same serial number
    gl_inst.nondet.prompt_results = [
        {"verdict": "VERIFIED_RETIRED", "standard": "VERRA", "serial_number": "VCS-DUPLICATE-001", "confidence": 95, "reason": "Same serial"}
    ]
    c.submit_and_verify(oid_2, "https://verra.org/reg/2", "")
    
    # SAFE OUTCOME FOR CONFLICTING ORDER:
    order_2 = c.orders[oid_2]
    assert order_2.status == "REFUNDED"
    assert order_2.verdict == "REJECTED_DUPLICATE_SERIAL"
    assert "Conflict" in order_2.reason
    assert c.total_locked_funds == 0
    
    # Verify Buyer 2 got 100% refund (6000), Seller 2 received 0
    buyer2_mock = gl_inst.get_contract_at(Address("0xBuyer2"))
    seller2_mock = gl_inst.get_contract_at(Address("0xSeller2"))
    assert 6000 in buyer2_mock.transfers
    assert len(seller2_mock.transfers) == 0


def test_insufficient_data_lifecycle_and_resubmission():
    """Verify that INSUFFICIENT_DATA preserves escrow and allows seller to correct/resubmit proof."""
    c = ContractClass("0xTreasury")
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(2000)
    oid = c.create_order("0xSeller", "Apple Inc", 100)
    
    # Step 1: Seller submits incomplete proof -> AI returns INSUFFICIENT_DATA
    gl_inst.nondet.web.render_result = "Partial screenshot: Retiree name missing, serial truncated"
    gl_inst.nondet.prompt_results = [
        {"verdict": "INSUFFICIENT_DATA", "standard": "VERRA", "serial_number": "", "confidence": 80, "reason": "Serial number missing from certificate image"}
    ]
    gl_inst.contracts.clear()
    gl_inst.message.sender_address = Address("0xSeller")
    c.submit_and_verify(oid, "https://verra.org/incomplete-1", "")
    
    order = c.orders[oid]
    assert order.status == "INSUFFICIENT_DATA"
    assert order.verdict == "INSUFFICIENT_DATA"
    assert c.total_locked_funds == 2000 # Escrow is safely preserved!
    
    # Step 2: Seller provides corrected official document link -> AI returns VERIFIED_RETIRED
    gl_inst.nondet.web.render_result = "Official Verra Certificate: Apple Inc, 100 Tonnes, RETIRED, Serial VCS-CORRECTED-888"
    gl_inst.nondet.prompt_results = [
        {"verdict": "VERIFIED_RETIRED", "standard": "VERRA", "serial_number": "VCS-CORRECTED-888", "confidence": 95, "reason": "Full corrected certificate verified"}
    ]
    c.submit_and_verify(oid, "https://verra.org/corrected-official-record", "https://verra.org/cert/full.pdf")
    
    order_after = c.orders[oid]
    assert order_after.status == "SETTLED"
    assert order_after.verdict == "VERIFIED_RETIRED"
    assert order_after.serial_number == "VCS-CORRECTED-888"
    assert c.total_locked_funds == 0
    
    seller_mock = gl_inst.get_contract_at(Address("0xSeller"))
    assert 1960 in seller_mock.transfers # 98% of 2000


def test_bounded_recovery_timeout_refund_after_deadline():
    """Verify bounded recovery path: buyer reclaims escrow after deadline expires without valid proof."""
    c = ContractClass("0xTreasury")
    gl_inst.block.timestamp = bigint(1700000000)
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(3000)
    oid = c.create_order("0xSeller", "Google LLC", 300, deadline_duration_seconds=86400) # 24h deadline
    
    # Seller submitted proof resulting in INSUFFICIENT_DATA
    gl_inst.nondet.prompt_results = [
        {"verdict": "INSUFFICIENT_DATA", "standard": "OTHER", "serial_number": "", "confidence": 75, "reason": "Incomplete proof"}
    ]
    gl_inst.message.sender_address = Address("0xSeller")
    c.submit_and_verify(oid, "https://example.com/missing-data", "")
    assert c.orders[oid].status == "INSUFFICIENT_DATA"
    
    # 1. Buyer tries to claim timeout refund before deadline -> REJECTED
    gl_inst.block.timestamp = bigint(1700040000) # Only ~11h passed (< 24h)
    gl_inst.message.sender_address = Address("0xBuyer")
    with pytest.raises(UserError, match="Order deadline has not expired yet"):
        c.claim_timeout_refund(oid)
        
    # 2. Non-buyer tries to claim timeout refund -> REJECTED
    gl_inst.block.timestamp = bigint(1700090000) # > 24h passed
    gl_inst.message.sender_address = Address("0xHacker")
    with pytest.raises(UserError, match="Only buyer can claim timeout refund"):
        c.claim_timeout_refund(oid)
        
    # 3. Buyer claims timeout refund after deadline -> SUCCESS
    gl_inst.contracts.clear()
    gl_inst.message.sender_address = Address("0xBuyer")
    c.claim_timeout_refund(oid)
    
    order = c.orders[oid]
    assert order.status == "REFUNDED"
    assert order.verdict == "TIMEOUT_REFUNDED"
    assert c.total_locked_funds == 0
    
    buyer_mock = gl_inst.get_contract_at(Address("0xBuyer"))
    assert 3000 in buyer_mock.transfers


def test_consensus_rejected_refund_path():
    """Verify REJECTED verdict triggers 100% refund to the buyer."""
    c = ContractClass("0xTreasury")
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(1000)
    oid = c.create_order("0xSeller", "Tesla Motors Inc", 500)
    
    gl_inst.nondet.web.render_result = "Verra Certificate #49129\nBeneficiary: Tesla Motors Inc\nQuantity: 500 Tonnes\nStatus: ACTIVE"
    gl_inst.nondet.prompt_results = [
        {"verdict": "REJECTED", "standard": "VERRA", "serial_number": "123", "confidence": 95, "reason": "Carbon credits are not retired. Status is ACTIVE."}
    ]
    gl_inst.contracts.clear()
    
    gl_inst.message.sender_address = Address("0xSeller")
    c.submit_and_verify(oid, "https://verra.org/registry/123", "")
    
    order = c.orders[oid]
    assert order.status == "REFUNDED"
    assert order.verdict == "REJECTED"
    assert c.total_locked_funds == 0
    
    buyer_mock = gl_inst.get_contract_at(Address("0xBuyer"))
    assert 1000 in buyer_mock.transfers


def test_consensus_abort_escalation_path():
    """Verify network failures or LLM aborts lock the funds and escalate order."""
    c = ContractClass("0xTreasury")
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(1000)
    oid = c.create_order("0xSeller", "Tesla Motors Inc", 500)
    
    gl_inst.nondet.web.render_result = "fail"
    gl_inst.contracts.clear()
    
    gl_inst.message.sender_address = Address("0xSeller")
    c.submit_and_verify(oid, "https://fail-url.com", "")
    
    order = c.orders[oid]
    assert order.status == "ESCALATED"
    assert order.verdict == "ABORT"
    assert c.total_locked_funds == 1000
    
    buyer_mock = gl_inst.get_contract_at(Address("0xBuyer"))
    seller_mock = gl_inst.get_contract_at(Address("0xSeller"))
    treasury_mock = gl_inst.get_contract_at(Address("0xTreasury"))
    assert len(buyer_mock.transfers) == 0
    assert len(seller_mock.transfers) == 0
    assert len(treasury_mock.transfers) == 0


def test_consensus_jailbreak_mismatch_escalates():
    """Verify that diverging multi-sample verdicts trigger an immediate ABORT escalation."""
    c = ContractClass("0xTreasury")
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(1000)
    oid = c.create_order("0xSeller", "Tesla Motors Inc", 500)
    
    gl_inst.nondet.web.render_result = "Verra Certificate #49129\nTesla Motors Inc\n500 Tonnes"
    gl_inst.nondet.prompt_results = [
        {"verdict": "VERIFIED_RETIRED", "standard": "VERRA", "serial_number": "123", "confidence": 80, "reason": "Looks good"},
        {"verdict": "REJECTED", "standard": "VERRA", "serial_number": "123", "confidence": 85, "reason": "Wait, looks wrong"}
    ]
    gl_inst.contracts.clear()
    
    gl_inst.message.sender_address = Address("0xSeller")
    c.submit_and_verify(oid, "https://verra.org/registry/123", "")
    
    order = c.orders[oid]
    assert order.status == "ESCALATED"
    assert order.verdict == "ABORT"
    assert order.reason == "multi_sample_divergence"
    assert c.total_locked_funds == 1000


def test_consensus_low_confidence_clamp():
    """Verify that a verdict with confidence < 65% is clamped to ABORT and escalated."""
    c = ContractClass("0xTreasury")
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(1000)
    oid = c.create_order("0xSeller", "Tesla Motors Inc", 500)
    
    gl_inst.nondet.web.render_result = "Verra Certificate #49129\nTesla Motors Inc\n500 Tonnes"
    gl_inst.nondet.prompt_results = [
        {"verdict": "VERIFIED_RETIRED", "standard": "VERRA", "serial_number": "123", "confidence": 50, "reason": "Unclear docs"}
    ]
    gl_inst.contracts.clear()
    
    gl_inst.message.sender_address = Address("0xSeller")
    c.submit_and_verify(oid, "https://verra.org/registry/123", "")
    
    order = c.orders[oid]
    assert order.status == "ESCALATED"
    assert order.verdict == "ABORT"
    assert "[low_confidence: 50%]" in order.reason
    assert c.total_locked_funds == 1000


def test_resolve_escalated_order_resolution_payouts():
    """Verify resolve_escalated_order releases funds/refunds correctly based on admin decisions."""
    c = ContractClass("0xTreasury")
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(1000)
    oid = c.create_order("0xSeller", "Tesla Motors Inc", 500)
    
    gl_inst.nondet.web.render_result = "fail"
    gl_inst.message.sender_address = Address("0xSeller")
    c.submit_and_verify(oid, "https://fail-url.com", "")
    
    assert c.orders[oid].status == "ESCALATED"
    
    gl_inst.message.sender_address = Address("0xHacker")
    with pytest.raises(UserError, match="Only buyer or treasury authority can resolve escalated order"):
        c.resolve_escalated_order(oid, release_to_seller=True)
        
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.contracts.clear()
    c.resolve_escalated_order(oid, release_to_seller=True)
    
    assert c.orders[oid].status == "SETTLED"
    assert c.orders[oid].verdict == "RESOLVED_MANUALLY_RELEASED"
    assert c.total_locked_funds == 0
    
    treasury_mock = gl_inst.get_contract_at(Address("0xTreasury"))
    seller_mock = gl_inst.get_contract_at(Address("0xSeller"))
    assert 20 in treasury_mock.transfers
    assert 980 in seller_mock.transfers
    
    # Reset for Case B: refund buyer
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(1000)
    oid_2 = c.create_order("0xSeller", "Tesla Motors Inc", 500)
    
    gl_inst.nondet.web.render_result = "fail"
    gl_inst.message.sender_address = Address("0xSeller")
    c.submit_and_verify(oid_2, "https://fail-url.com", "")
    
    gl_inst.message.sender_address = Address("0xTreasury")
    gl_inst.contracts.clear()
    c.resolve_escalated_order(oid_2, release_to_seller=False)
    
    assert c.orders[oid_2].status == "REFUNDED"
    assert c.orders[oid_2].verdict == "RESOLVED_MANUALLY_REFUNDED"
    assert c.total_locked_funds == 0
    
    buyer_mock = gl_inst.get_contract_at(Address("0xBuyer"))
    assert 1000 in buyer_mock.transfers


def test_transaction_rollback_defense():
    """Verify that failed transactions revert state updates cleanly (state rollback)."""
    c = ContractClass("0xTreasury")
    
    gl_inst.message.sender_address = Address("0xBuyer")
    gl_inst.message.value = bigint(1000)
    oid = c.create_order("0xSeller", "Tesla Motors Inc", 500)
    
    snapshot = clone_orders(c.orders)
    assert c.orders[oid].status == "OPEN"
    
    gl_inst.message.sender_address = Address("0xHacker")
    with pytest.raises(UserError):
        c.cancel_order(oid)
        
    assert len(c.orders) == len(snapshot)
    assert c.orders[oid].status == "OPEN"
    assert c.orders[oid].verdict == ""
    assert c.total_locked_funds == 1000


if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING UNIT TESTS FOR CARBONOFFSETSHIELD CONTRACT")
    print("=" * 60)
    test_contract_schema_and_integrity()
    test_init_validations()
    test_create_order_happy_path_and_validations()
    test_cancel_order_payout_and_guards()
    test_consensus_verified_retired_payout_split()
    test_duplicate_serial_prevention_safely_refunds_conflicting_order()
    test_insufficient_data_lifecycle_and_resubmission()
    test_bounded_recovery_timeout_refund_after_deadline()
    test_consensus_rejected_refund_path()
    test_consensus_abort_escalation_path()
    test_consensus_jailbreak_mismatch_escalates()
    test_consensus_low_confidence_clamp()
    test_resolve_escalated_order_resolution_payouts()
    test_transaction_rollback_defense()
    print("\n==================================================")
    print("[OK] ALL CONTRACT UNIT TESTS COMPLETED SUCCESSFULLY!")
    print("==================================================")
