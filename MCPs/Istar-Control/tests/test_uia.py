import pytest

from istar_control import uia


class Node:
    def __init__(self, ident, children=(), *, password=False, enabled=True):
        self.ident, self.children = ident, list(children)
        self.password, self.enabled = password, enabled


class Backend:
    def __init__(self, root):
        self.tree = root
        self.checks = []
        self.invoked = []
        self.siblings = {}

        def index(node):
            for i, child in enumerate(node.children):
                self.siblings[child] = (
                    node.children[i + 1] if i + 1 < len(node.children) else None
                )
                index(child)

        index(root)

    def check(self, target, foreground=False):
        self.checks.append(foreground)

    def root(self, target):
        return self.tree

    def child(self, node):
        return node.children[0] if node.children else None

    def sibling(self, node):
        return self.siblings.get(node)

    def describe(self, node):
        if node.password:
            return None
        return {
            "name": node.ident,
            "automation_id": node.ident,
            "control_type": 50000,
            "enabled": node.enabled,
        }

    def invoke(self, node):
        self.invoked.append(node.ident)


@pytest.fixture
def setup(monkeypatch):
    def install(root):
        backend = Backend(root)
        monkeypatch.setattr(
            uia, "_run", lambda operation, timeout=10.0: operation(backend)
        )
        return backend

    return install


TARGET = uia.WindowTarget(100, 200)


def test_observe_bounded_and_password_subtree_hidden(setup):
    setup(
        Node(
            "root", [Node("password", [Node("secret")], password=True), Node("button")]
        )
    )
    result = uia.observe(TARGET, limit=3)
    assert [e["name"] for e in result["elements"]] == ["root", "button"]
    assert not result["truncated"]
    assert uia.observe(TARGET, limit=2)["truncated"]


def test_invoke_unique_and_foreground_checked_twice(setup):
    backend = setup(Node("root", [Node("button")]))
    uia.invoke(TARGET, uia.Selector(automation_id="button"))
    assert backend.checks == [True, True]
    assert backend.invoked == ["button"]


@pytest.mark.parametrize(
    "children,limit,depth",
    [
        ([Node("button"), Node("button")], 10, 8),
        ([Node("button"), Node("other")], 2, 8),
        ([Node("button", [Node("child")])], 10, 1),
        ([Node("button", enabled=False)], 10, 8),
    ],
)
def test_invoke_fails_closed(setup, children, limit, depth):
    backend = setup(Node("root", children))
    with pytest.raises(uia.UIAError):
        uia.invoke(
            TARGET, uia.Selector(automation_id="button"), limit=limit, depth=depth
        )
    assert backend.invoked == []


def test_focus_loss_before_action(setup):
    backend = setup(Node("root", [Node("button")]))

    def check(target, foreground=False):
        backend.checks.append(foreground)
        if len(backend.checks) == 2:
            raise uia.UIAError("target window is not foreground")

    backend.check = check
    with pytest.raises(uia.UIAError):
        uia.invoke(TARGET, uia.Selector(automation_id="button"))
    assert not backend.invoked


@pytest.mark.parametrize(
    "target,selector,limit,depth",
    [
        (uia.WindowTarget(True, 2), uia.Selector(automation_id="x"), 1, 1),
        (TARGET, uia.Selector(), 1, 1),
        (TARGET, uia.Selector(automation_id="x" * 257), 1, 1),
        (TARGET, uia.Selector(control_type=True), 1, 1),
        (TARGET, uia.Selector(control_type=50000), 501, 1),
        (TARGET, uia.Selector(control_type=50000), 1, 21),
    ],
)
def test_invalid_arguments(target, selector, limit, depth):
    with pytest.raises(ValueError):
        uia.invoke(target, selector, limit=limit, depth=depth)


def test_set_value_and_toggle_unique(setup):
    backend = setup(Node("root", [Node("field")]))

    backend.values = []
    backend.set_value = lambda node, value: backend.values.append((node.ident, value))

    backend.toggles = []
    backend.toggle = lambda node: backend.toggles.append(node.ident)

    uia.set_value(TARGET, uia.Selector(automation_id="field"), "hello")
    uia.toggle(TARGET, uia.Selector(automation_id="field"))
    assert backend.values == [("field", "hello")]
    assert backend.toggles == ["field"]
    assert backend.checks == [True, True, True, True]


def test_set_value_rejects_long_values(setup):
    backend = setup(Node("root", [Node("field")]))
    with pytest.raises(ValueError):
        uia.set_value(TARGET, uia.Selector(automation_id="field"), "x" * 4097)
    with pytest.raises(ValueError):
        uia.set_value(TARGET, uia.Selector(automation_id="field"), "")
    assert not hasattr(backend, "values")


def test_timeout_validation(setup):
    setup(Node("root", [Node("button")]))
    with pytest.raises(ValueError):
        uia.observe(TARGET, timeout=0)
    with pytest.raises(ValueError):
        uia.observe(TARGET, timeout=61)
    with pytest.raises(ValueError):
        uia.invoke(TARGET, uia.Selector(automation_id="button"), timeout=True)
