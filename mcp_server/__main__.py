from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_server.tools import (
    get_student_state,
    save_attempt,
    schedule_review,
    search_course_material,
)

mcp = FastMCP("StudyHub")
mcp.tool()(search_course_material)
mcp.tool()(get_student_state)
mcp.tool()(save_attempt)
mcp.tool()(schedule_review)


if __name__ == "__main__":
    mcp.run(transport="stdio")
