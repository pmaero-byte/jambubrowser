"""
ALFWorld-style benchmark tasks — text-based embodied household agent.

Based on the ALFWorld benchmark (https://alfworld.github.io/).
Tasks simulate interacting with a text-based household environment where the
agent must plan and execute sequences of actions (pick, place, open, close,
clean, heat, cool, examine) to achieve goals.

All tasks are self-contained QA pairs with verifiable action sequences.
The agent must reason about object states, locations, and preconditions.
"""

from __future__ import annotations

from ..harness import Task, register_task

SUITE = "alfworld"


# ── Pick & Place tasks ───────────────────────────────────────────

register_task(Task(
    id="alf.pick_place.simple",
    suite=SUITE,
    prompt=(
        "You are in a kitchen. You see: a red apple on the counter, an empty fridge, "
        "a microwave on the counter, a sink. You are holding nothing.\n\n"
        "Your task: Put the apple in the fridge.\n\n"
        "Available actions: go to <object>, take <object>, put <object> on/in <object>, "
        "open <object>, close <object>. Respond with the sequence of actions (one per line)."
    ),
    expected=["take apple", "open fridge", "put apple in fridge", "close fridge"],
    category="browser",
    difficulty=2,
    timeout_seconds=20,
    max_steps=4,
    use_agent=True,
    system="Respond with one action per line. No explanation.",
))

register_task(Task(
    id="alf.pick_place.multi",
    suite=SUITE,
    prompt=(
        "You are in a living room. You see: a blue book on the coffee table, "
        "a red mug on the shelf, a green vase on the floor, a wooden desk. "
        "You are holding nothing.\n\n"
        "Your task: Put the book on the desk and the mug on the coffee table.\n\n"
        "Available actions: go to <object>, take <object>, put <object> on/in <object>. "
        "Respond with the sequence of actions."
    ),
    expected=["take book", "put book on desk", "take mug", "put mug on coffee table"],
    category="browser",
    difficulty=2,
    timeout_seconds=25,
    max_steps=5,
    use_agent=True,
    system="Respond with one action per line.",
))

register_task(Task(
    id="alf.pick_place.container",
    suite=SUITE,
    prompt=(
        "You are in a bedroom. You see: a closed drawer, a lamp on the nightstand, "
        "a watch inside the drawer (the drawer is closed), a bed. "
        "You are holding nothing.\n\n"
        "Your task: Get the watch and put it on the nightstand.\n\n"
        "Available actions: go to <object>, take <object>, put <object> on/in <object>, "
        "open <object>, close <object>. Respond with the sequence of actions."
    ),
    expected=["open drawer", "take watch", "put watch on nightstand"],
    category="browser",
    difficulty=3,
    timeout_seconds=25,
    max_steps=5,
    use_agent=True,
    system="Respond with one action per line.",
))


# ── Clean & Heat tasks ───────────────────────────────────────────

register_task(Task(
    id="alf.clean.lettuce",
    suite=SUITE,
    prompt=(
        "You are in a kitchen. You see: a dirty lettuce on the counter, "
        "a sponge on the counter, a sink with running water, a fridge, a cupboard. "
        "You are holding nothing.\n\n"
        "Your task: Clean the lettuce and put it in the fridge.\n\n"
        "Available actions: go to <object>, take <object>, put <object> on/in <object>, "
        "use <object> on <object>, clean <object> with <object> at <object>, "
        "open <object>, close <object>. Respond with the sequence of actions."
    ),
    expected=["take lettuce", "clean lettuce with sponge at sink", "open fridge", "put lettuce in fridge"],
    category="browser",
    difficulty=2,
    timeout_seconds=25,
    max_steps=5,
    use_agent=True,
    system="Respond with one action per line.",
))

register_task(Task(
    id="alf.heat.potato",
    suite=SUITE,
    prompt=(
        "You are in a kitchen. You see: a raw potato on the counter, "
        "a microwave on the counter (closed), a plate in the cupboard. "
        "You are holding nothing.\n\n"
        "Your task: Heat the potato using the microwave.\n\n"
        "Available actions: go to <object>, take <object>, put <object> on/in <object>, "
        "open <object>, close <object>, use <object>. Respond with the sequence of actions."
    ),
    expected=["take potato", "open microwave", "put potato in microwave", "close microwave", "use microwave"],
    category="browser",
    difficulty=2,
    timeout_seconds=25,
    max_steps=6,
    use_agent=True,
    system="Respond with one action per line.",
))

register_task(Task(
    id="alf.cool.milk",
    suite=SUITE,
    prompt=(
        "You are in a kitchen. You see: a carton of milk on the counter (room temperature), "
        "a fridge (closed), a shelf inside the fridge. You are holding nothing.\n\n"
        "Your task: Cool the milk by putting it in the fridge.\n\n"
        "Available actions: go to <object>, take <object>, put <object> on/in <object>, "
        "open <object>, close <object>. Respond with the sequence of actions."
    ),
    expected=["take milk", "open fridge", "put milk in fridge", "close fridge"],
    category="browser",
    difficulty=1,
    timeout_seconds=20,
    max_steps=4,
    use_agent=True,
    system="Respond with one action per line.",
))


# ── Examine & Two-object tasks ───────────────────────────────────

register_task(Task(
    id="alf.examine.clock",
    suite=SUITE,
    prompt=(
        "You are in a dark bedroom. You see: an alarm clock on the nightstand, "
        "a desklamp on the desk (turned off), a bed, a window. "
        "You are holding nothing.\n\n"
        "Your task: Examine the alarm clock under the desklamp.\n\n"
        "Available actions: go to <object>, take <object>, put <object> on/in <object>, "
        "turn on <object>, use <object> on <object>, look at <object>. "
        "Respond with the sequence of actions."
    ),
    expected=["turn on desklamp", "look at alarm clock"],
    category="browser",
    difficulty=2,
    timeout_seconds=20,
    max_steps=4,
    use_agent=True,
    system="Respond with one action per line.",
))

register_task(Task(
    id="alf.two_object.cup_bowl",
    suite=SUITE,
    prompt=(
        "You are in a dining room. You see: a clean cup on the table, "
        "a clean bowl in the cupboard, a spoon in the drawer, a dining table. "
        "The drawer is closed. You are holding nothing.\n\n"
        "Your task: Place the cup and the spoon on the dining table.\n\n"
        "Available actions: go to <object>, take <object>, put <object> on/in <object>, "
        "open <object>, close <object>. Respond with the sequence of actions."
    ),
    expected=["take cup", "put cup on dining table", "open drawer", "take spoon", "put spoon on dining table"],
    category="browser",
    difficulty=3,
    timeout_seconds=30,
    max_steps=6,
    use_agent=True,
    system="Respond with one action per line.",
))

register_task(Task(
    id="alf.obstructed.towel",
    suite=SUITE,
    prompt=(
        "You are in a bathroom. You see: a towel inside a closed cabinet, "
        "a bathtub, a sink. You are holding nothing.\n\n"
        "Your task: Put the towel in the bathtub.\n\n"
        "Available actions: go to <object>, take <object>, put <object> on/in <object>, "
        "open <object>, close <object>. Respond with the sequence of actions."
    ),
    expected=["open cabinet", "take towel", "put towel in bathtub"],
    category="browser",
    difficulty=2,
    timeout_seconds=20,
    max_steps=4,
    use_agent=True,
    system="Respond with one action per line.",
))
