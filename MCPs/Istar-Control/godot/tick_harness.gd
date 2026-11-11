extends SceneTree

# Opt-in adapter contract: a project-owned RefCounted script with
# setup(seed: int), step(tick: int, actions: Dictionary), snapshot() -> Dictionary.
# Invocation: godot --headless --path PROJECT --script /absolute/tick_harness.gd
#   -- res://fixture.gd '{"ticks":2,"seed":1,"events":[{"tick":0,"action":"right","pressed":true}]}'
# Steps are synchronous calls, NOT engine physics frames. The fixture must implement
# its own fixed-step logic and avoid real-time, asynchronous and uncontrolled RNG.
# This is not a sandbox or a promise of cross-hardware reproducibility: loading a
# project runs project code and the fixture is trusted. No network port is opened.

const PREFIX := "ISTAR_TICK_RESULT="
const MAX_TICKS := 10000
const MAX_EVENTS := 10000
const MAX_PLAN_BYTES := 262144

func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() != 2 or not args[0].begins_with("res://") or not args[0].ends_with(".gd"):
		_fail("expected fixture res:// script and JSON plan")
		return
	if args[1].to_utf8_buffer().size() > MAX_PLAN_BYTES:
		_fail("plan too large")
		return
	var parser := JSON.new()
	if parser.parse(args[1]) != OK or not parser.data is Dictionary:
		_fail("invalid JSON plan")
		return
	var plan: Dictionary = parser.data
	if not _valid_plan(plan):
		_fail("invalid plan")
		return
	var script = load(args[0])
	if not script is Script or not script.can_instantiate():
		_fail("fixture cannot be instantiated")
		return
	var fixture = script.new()
	if not fixture is RefCounted or not fixture.has_method("setup") or not fixture.has_method("step") or not fixture.has_method("snapshot"):
		_fail("fixture must be RefCounted and implement setup, step and snapshot")
		return
	fixture.setup(int(plan.seed))
	var actions := {}
	var by_tick := {}
	for event in plan.events:
		var tick := int(event.tick)
		if not by_tick.has(tick):
			by_tick[tick] = []
		by_tick[tick].append(event)
	for tick in range(int(plan.ticks)):
		for event in by_tick.get(tick, []):
			actions[event.action] = event.pressed
		fixture.step(tick, actions.duplicate())
	var state = fixture.snapshot()
	if not state is Dictionary or not _json_value(state, 0):
		_fail("snapshot must contain only finite JSON values")
		return
	print(PREFIX + JSON.stringify({"ok": true, "ticks": int(plan.ticks), "state": state}))
	quit(0)

func _integer(value, minimum: int, maximum: int) -> bool:
	return (typeof(value) == TYPE_INT or typeof(value) == TYPE_FLOAT) and is_finite(value) and value == floor(value) and value >= minimum and value <= maximum

func _valid_plan(plan: Dictionary) -> bool:
	if plan.size() != 3 or not plan.has_all(["ticks", "seed", "events"]):
		return false
	if not _integer(plan.ticks, 0, MAX_TICKS) or not _integer(plan.seed, 0, 2147483647) or not plan.events is Array or plan.events.size() > MAX_EVENTS:
		return false
	for event in plan.events:
		if not event is Dictionary or event.size() != 3 or not event.has_all(["tick", "action", "pressed"]):
			return false
		if not _integer(event.tick, 0, int(plan.ticks) - 1) or not event.action is String or event.action.length() < 1 or event.action.length() > 64 or not event.pressed is bool:
			return false
	return true

func _json_value(value, depth: int) -> bool:
	if depth > 16:
		return false
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return true
		TYPE_FLOAT:
			return is_finite(value)
		TYPE_ARRAY:
			if value.size() > 10000: return false
			for item in value:
				if not _json_value(item, depth + 1): return false
			return true
		TYPE_DICTIONARY:
			if value.size() > 10000: return false
			for key in value:
				if not key is String or not _json_value(value[key], depth + 1): return false
			return true
	return false

func _fail(message: String) -> void:
	print(PREFIX + JSON.stringify({"ok": false, "error": message}))
	quit(2)
