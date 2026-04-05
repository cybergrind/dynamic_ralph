"""Tests for multi_agent.testing — TestingBackend and AgentScript."""

from __future__ import annotations

from pathlib import Path

from multi_agent.testing import AgentScript, TestingBackend


class TestAgentScript:
    def test_to_result_defaults(self):
        script = AgentScript()
        result = script.to_result()
        assert result.exit_code == 0
        assert result.completion_status == 'end_turn'
        assert result.final_response == ''
        assert result.structured_output is None

    def test_to_result_with_response(self):
        script = AgentScript(response='hello world')
        result = script.to_result()
        assert result.final_response == 'hello world'
        assert result.full_response == 'hello world'

    def test_to_result_with_structured_output(self):
        so = {'winner': 'A', 'reason': 'simplicity'}
        script = AgentScript(structured_output=so)
        result = script.to_result()
        assert result.structured_output == so

    def test_to_result_failure(self):
        script = AgentScript(exit_code=1)
        result = script.to_result()
        assert result.exit_code == 1
        assert result.completion_status == 'error'

    def test_to_result_timeout(self):
        script = AgentScript(timed_out=True)
        result = script.to_result()
        assert result.timed_out is True


class TestTestingBackend:
    def test_returns_scripted_results(self, tmp_path: Path):
        from multi_agent.backend import LaunchConfig
        from multi_agent.parallel import launch_parallel_agents

        scripts = {
            'A': AgentScript(response='proposal A'),
            'B': AgentScript(response='proposal B'),
        }
        backend = TestingBackend(scripts)
        config = LaunchConfig(backend=backend, log_dir=tmp_path, timeout=30)

        results = launch_parallel_agents({'A': 'prompt A', 'B': 'prompt B'}, config=config)

        assert results['A'].final_response == 'proposal A'
        assert results['B'].final_response == 'proposal B'
        assert results['A'].exit_code == 0
        assert results['B'].exit_code == 0

    def test_missing_script_crashes(self, tmp_path: Path):
        from multi_agent.backend import LaunchConfig
        from multi_agent.parallel import launch_parallel_agents

        scripts = {'A': AgentScript(response='ok')}
        backend = TestingBackend(scripts)
        config = LaunchConfig(backend=backend, log_dir=tmp_path, timeout=30)

        results = launch_parallel_agents({'A': 'p', 'B': 'p'}, config=config)
        assert results['A'].exit_code == 0
        assert results['B'].completion_status == 'crashed'
