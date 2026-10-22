"""A Strands agent on Amazon Bedrock, working through the gate: it can read freely and ask for writes, never do them.

    pip install "git+https://github.com/Prashant-thakur77/confirm-gate-mcp" strands-agents
    # once it is on PyPI: pip install confirm-gate-mcp strands-agents
    confirm-gate serve --upstream-cmd "npx -y @modelcontextprotocol/server-filesystem /tmp/sandbox" \
        --strict --reads "read_*,list_*,search_*,get_*,directory_tree"
    GATE_TOKEN=<printed token> python examples/strands_agent.py "Tidy /tmp/sandbox: move every .tmp file into old/"

Open the approval page the server printed, pair with the code, and approve or decline each card there. The agent's
reply says what is waiting; nothing on disk changes until you tap.
"""
import os
import sys

from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

GATE = os.environ.get("GATE_URL", "http://127.0.0.1:8800/mcp")
MODEL = os.environ.get("BEDROCK_MODEL_ID", "us.amazon.nova-2-lite-v1:0")

gate = MCPClient(url=GATE, headers={"Authorization": f"Bearer {os.environ['GATE_TOKEN']}"})
with gate:
    agent = Agent(
        model=BedrockModel(model_id=MODEL, region_name=os.environ.get("AWS_REGION", "us-east-1")),
        tools=gate.list_tools_sync(),
        system_prompt="Changes you request wait for the user to approve them on their own device. "
                      "Say plainly what is waiting; never claim a change happened until pending_status says so.",
    )
    agent(" ".join(sys.argv[1:]) or "What's in /tmp/sandbox? Then move anything ending in .tmp into /tmp/sandbox/old.")
