"""Shared code for the ralph multi-agent workflow."""

from multi_agent.backend import AgentBackend, AgentEvent, AgentResult, get_backend
from multi_agent.codex_prompts import (
    build_debate_prompt,
    build_propose_prompt,
    build_vote_prompt,
    check_quality_gate,
    concatenate_debate,
    concatenate_proposals,
    load_codex,
    load_identity,
)
from multi_agent.compose import compose, compose_bare
from multi_agent.constants import (
    AGENT_BACKEND,
    COMPOSE_FILE,
    ENV_FILE,
    GIT_AUTHOR_EMAIL,
    GIT_AUTHOR_NAME,
    GIT_EMAIL,
    INFRA_SERVICES,
    MULTI_AGENT_MAX_WORKERS,
    RALPH_IMAGE,
    RALPH_INTERNAL_DOCS,
    RALPH_MODE,
    SERVICE,
    get_git_author_identity,
)
from multi_agent.docker import build_image, docker_sock_gid, image_exists
from multi_agent.filelock import FileLock, FileLockTimeout
from multi_agent.models import FlatStory, Prd, UserStory, parse_prd
from multi_agent.orchestrate import run_multi_agent, validate_frame
from multi_agent.parallel import launch_parallel_agents
from multi_agent.parsing import (
    ParseDiagnostic,
    VoteResult,
    parse_proposal,
    parse_sections,
    parse_vote,
    summarize_phase_health,
    write_phase_diagnostics,
)
from multi_agent.prd import find_next_story, load_prd, load_prd_model, save_prd
from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS, build_system_prompt
from multi_agent.stream import display_agent_event, display_event
from multi_agent.tally import (
    DecisionRecord,
    Frame,
    Tally,
    build_decision,
    build_iteration_context,
    compute_tally,
    detect_veto,
)


__all__ = [
    'AGENT_BACKEND',
    'BASE_AGENT_INSTRUCTIONS',
    'COMPOSE_FILE',
    'ENV_FILE',
    'GIT_AUTHOR_EMAIL',
    'GIT_AUTHOR_NAME',
    'GIT_EMAIL',
    'INFRA_SERVICES',
    'MULTI_AGENT_MAX_WORKERS',
    'RALPH_IMAGE',
    'RALPH_INTERNAL_DOCS',
    'RALPH_MODE',
    'SERVICE',
    'AgentBackend',
    'AgentEvent',
    'AgentResult',
    'DecisionRecord',
    'FileLock',
    'FileLockTimeout',
    'FlatStory',
    'Frame',
    'ParseDiagnostic',
    'Prd',
    'Tally',
    'UserStory',
    'VoteResult',
    'build_debate_prompt',
    'build_decision',
    'build_image',
    'build_iteration_context',
    'build_propose_prompt',
    'build_system_prompt',
    'build_vote_prompt',
    'check_quality_gate',
    'compose',
    'compose_bare',
    'compute_tally',
    'concatenate_debate',
    'concatenate_proposals',
    'detect_veto',
    'display_agent_event',
    'display_event',
    'docker_sock_gid',
    'find_next_story',
    'get_backend',
    'get_git_author_identity',
    'image_exists',
    'launch_parallel_agents',
    'load_codex',
    'load_identity',
    'load_prd',
    'load_prd_model',
    'parse_prd',
    'parse_proposal',
    'parse_sections',
    'parse_vote',
    'run_multi_agent',
    'save_prd',
    'summarize_phase_health',
    'validate_frame',
    'write_phase_diagnostics',
]
