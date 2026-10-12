"""
Minimal offline stub of the `genlayer` SDK. Test-only shim, not part of the
deployable contract.

It reproduces just enough of the SDK surface to import and exercise the
contract in plain Python: storage containers, Address, u256, the public
decorators, `gl.message`, `gl.vm.run_nondet_unsafe` (leader first, then the
validator, in-process), `gl.nondet.web.render`, `gl.nondet.exec_prompt` and
the EVM value-transfer ledger. Tests patch the fetch and model calls per case.

It does not simulate real networking, a real model, multi-validator
consensus or GenVM's clock. Those need GenLayer Studio; see README.md.
"""

__all__ = ["gl", "TreeMap", "u256", "DynArray", "i256", "bigint", "Address"]


class _Subscriptable:
    def __class_getitem__(cls, item):
        return cls


class TreeMap(_Subscriptable, dict):
    """Persistent map stand-in: behaves like a dict."""


class DynArray(_Subscriptable, list):
    """Persistent array stand-in: behaves like a list."""


class u256(int):
    """Fixed-width unsigned integer stand-in."""


class i256(int):
    """Fixed-width signed integer stand-in."""


class bigint(int):
    """Arbitrary-precision integer stand-in."""


class Address:
    """A 0x-prefixed 20-byte hex address, compared case-insensitively."""

    def __init__(self, value):
        text = str(value)
        if not text.startswith("0x") or len(text) != 42:
            raise ValueError(f"invalid address: {text!r}")
        int(text[2:], 16)
        self._value = text

    def __str__(self):
        return self._value

    def __repr__(self):
        return f"Address({self._value!r})"

    def __eq__(self, other):
        return str(self).lower() == str(other).lower()

    def __hash__(self):
        return hash(str(self).lower())


class UserError(Exception):
    """gl.vm.UserError stand-in."""


class _Vm:
    UserError = UserError

    class Return:
        def __init__(self, calldata):
            self.calldata = calldata

    @staticmethod
    def run_nondet_unsafe(leader_fn, validator_fn):
        result = leader_fn()
        if not validator_fn(_Vm.Return(result)):
            raise UserError("validator rejected leader result")
        return result


class _WriteDecorator:
    def __call__(self, fn):
        return fn

    @staticmethod
    def payable(fn):
        return fn


class _Public:
    write = _WriteDecorator()

    @staticmethod
    def view(fn):
        return fn


class _NondetWeb:
    @staticmethod
    def render(url, mode="text"):
        raise NotImplementedError("gl.nondet.web.render must be patched in tests")


class _Nondet:
    web = _NondetWeb()

    @staticmethod
    def exec_prompt(prompt, response_format="text"):
        raise NotImplementedError("gl.nondet.exec_prompt must be patched in tests")


class _EqPrinciple:
    @staticmethod
    def strict_eq(fn):
        return fn()


class _Message:
    sender_address = None
    value = u256(0)


class _Contract:
    """Pre-populates annotated TreeMap/DynArray fields with empty instances,
    as the real runtime does, and gives each instance a `balance`."""

    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        for klass in reversed(cls.__mro__):
            for name, annotation in vars(klass).get("__annotations__", {}).items():
                if isinstance(annotation, type) and issubclass(annotation, (TreeMap, DynArray)):
                    setattr(instance, name, annotation())
        instance.balance = u256(0)
        return instance

    def __init__(self, *args, **kwargs):
        pass


class _EvmProxy:
    def __init__(self, address):
        self.address = address

    def emit_transfer(self, value):
        _Evm.transfers.append({"to": str(self.address), "value": u256(value)})


class _Evm:
    transfers = []

    @staticmethod
    def contract_interface(cls):
        return _EvmProxy


class _GL:
    Contract = _Contract
    public = _Public()
    nondet = _Nondet()
    eq_principle = _EqPrinciple()
    vm = _Vm()
    message = _Message()
    evm = _Evm()


gl = _GL()
