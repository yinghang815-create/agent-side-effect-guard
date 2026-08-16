from agent_side_effect_guard import SideEffectGuard

guard = SideEffectGuard("agent-effects.db")


def send_email():
    # Replace with the real external API call.
    return {"message_id": "example-123"}


result = guard.run(
    "send_email",
    "welcome:user-42",
    send_email,
    payload={"template": "welcome", "user_id": 42},
)
print(result)
