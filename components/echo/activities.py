from proteus import activity


@activity
async def echo(arg: dict) -> dict:
    return {"text": arg.get("text", ""), "echo": True}
