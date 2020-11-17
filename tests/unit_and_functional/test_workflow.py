"""
Test Bodywork workflow execution.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import Iterable

import requests
from pytest import raises
from _pytest.capture import CaptureFixture

from bodywork.config import BodyworkConfig
from bodywork.constants import STAGE_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME
from bodywork.exceptions import (
    BodyworkProjectConfigError,
    BodyworkWorkflowExecutionError
)
from bodywork.stage import BatchStage, ServiceStage
from bodywork.workflow import (
    BodyworkProject,
    image_exists_on_dockerhub,
    parse_dockerhub_image_string,
    run_workflow,
    _parse_dag_definition,
    _get_workflow_stages,
    _print_logs_to_stdout
)


def test_bodywork_project_config_validation(
    project_repo_location: Path
):
    with raises(BodyworkProjectConfigError, match='PROJECT_NAME'):
        BodyworkProject(project_repo_location / 'bad_project_name_bodywork.ini')
    with raises(BodyworkProjectConfigError, match='DOCKER_IMAGE'):
        BodyworkProject(project_repo_location / 'bad_docker_image_bodywork.ini')
    with raises(BodyworkProjectConfigError, match='DAG'):
        BodyworkProject(project_repo_location / 'bad_DAG_bodywork.ini')
    with raises(BodyworkProjectConfigError, match='LOG_LEVEL'):
        BodyworkProject(project_repo_location / 'bad_log_level_bodywork.ini')


def test_bodywork_project_config_returns_correct_data(
    project_repo_location: Path
):
    project_data = BodyworkProject(project_repo_location / PROJECT_CONFIG_FILENAME)
    assert project_data.name == 'bodywork-test-project'
    assert project_data.docker_image == 'alexioannides/bodywork:latest'
    assert project_data.dag == 'stage_1_good >> stage_4_good,stage_5_good'
    assert project_data.log_level == 'INFO'


def test_parse_dag_definition_parses_multi_stage_dags():
    dag_definition = 'stage_1 >> stage_2,stage_3 >> stage_4'
    parsed_dag_structure = _parse_dag_definition(dag_definition)
    expected_dag_structure = [
        ['stage_1'],
        ['stage_2', 'stage_3'],
        ['stage_4']
    ]
    assert parsed_dag_structure == expected_dag_structure


def test_parse_dag_definition_parses_single_stage_dags():
    dag_definition = 'stage_1'
    parsed_dag_structure = _parse_dag_definition(dag_definition)
    expected_dag_structure = [['stage_1']]
    assert parsed_dag_structure == expected_dag_structure


def test_parse_dag_definition_raises_invalid_dag_definition_exceptions():
    dag_definition = 'stage_1 >> ,stage_3 >> stage_4'
    with raises(ValueError, match='null stages found in step 2'):
        _parse_dag_definition(dag_definition)


def test_get_workflow_stages_raises_exception_for_invalid_stages(
    project_repo_location: Path,
):
    dag = [['stage_1_good'], ['stage_2_bad_config']]
    with raises(RuntimeError, match='stage_2_bad_config'):
        _get_workflow_stages(dag, project_repo_location)


def test_get_workflow_stages_return_valid_stage_info(
    project_repo_location: Path,
):
    dag = [['stage_1_good'], ['stage_4_good', 'stage_5_good']]

    path_to_stage_1_dir = project_repo_location / 'stage_1_good'
    stage_1_info = BatchStage(
        'stage_1_good',
        BodyworkConfig(path_to_stage_1_dir / STAGE_CONFIG_FILENAME),
        path_to_stage_1_dir
    )

    path_to_stage_4_dir = project_repo_location / 'stage_4_good'
    stage_4_info = BatchStage(
        'stage_4_good',
        BodyworkConfig(path_to_stage_4_dir / STAGE_CONFIG_FILENAME),
        path_to_stage_4_dir
    )

    path_to_stage_5_dir = project_repo_location / 'stage_5_good'
    stage_5_info = ServiceStage(
        'stage_5_good',
        BodyworkConfig(path_to_stage_5_dir / STAGE_CONFIG_FILENAME),
        path_to_stage_5_dir
    )

    all_stage_info = _get_workflow_stages(dag, project_repo_location)
    assert len(all_stage_info) == 3
    assert all_stage_info['stage_1_good'] == stage_1_info
    assert all_stage_info['stage_4_good'] == stage_4_info
    assert all_stage_info['stage_5_good'] == stage_5_info


@patch('requests.Session')
def test_image_exists_on_dockerhub_handles_connection_error(
    mock_requests_session: MagicMock
):
    mock_requests_session().get.side_effect = requests.exceptions.ConnectionError
    with raises(RuntimeError, match='cannot connect to'):
        image_exists_on_dockerhub('alexioannides/bodywork', 'latest')


@patch('requests.Session')
def test_image_exists_on_dockerhub_handles_correctly_identifies_image_repos(
    mock_requests_session: MagicMock
):
    mock_requests_session().get.return_value = requests.Response()

    mock_requests_session().get.return_value.status_code = 200
    assert image_exists_on_dockerhub('alexioannides/bodywork', 'v1') is True

    mock_requests_session().get.return_value.status_code = 404
    assert image_exists_on_dockerhub('alexioannides/bodywork', 'x') is False


def test_parse_dockerhub_image_string_raises_exception_for_invalid_strings():
    with raises(
        ValueError,
        match=f'invalid DOCKER_IMAGE specified in {PROJECT_CONFIG_FILENAME}'
    ):
        parse_dockerhub_image_string('alexioannides-bodywork-stage-runner:latest')
        parse_dockerhub_image_string('alexioannides/bodywork:lat:st')


def test_parse_dockerhub_image_string_parses_valid_strings():
    assert (parse_dockerhub_image_string('alexioannides/bodywork:0.0.1')
            == ('alexioannides/bodywork', '0.0.1'))
    assert (parse_dockerhub_image_string('alexioannides/bodywork')
            == ('alexioannides/bodywork', 'latest'))


@patch('bodywork.workflow.k8s')
def test_run_workflow_raises_exception_if_namespace_does_not_exist(
    mock_k8s: MagicMock,
    setup_bodywork_test_project: Iterable[bool],
    project_repo_location: Path,
):
    mock_k8s.namespace_exists.return_value = False
    with raises(BodyworkWorkflowExecutionError, match='not a valid namespace'):
        run_workflow('foo_bar_foo_993', project_repo_location)


@patch('bodywork.workflow.k8s')
def test_print_logs_to_stdout(mock_k8s: MagicMock, capsys: CaptureFixture):
    mock_k8s.get_latest_pod_name.return_value = 'bodywork-test-project--stage-1'
    mock_k8s.get_pod_logs.return_value = 'foo-bar'
    _print_logs_to_stdout('the-namespace', 'bodywork-test-project--stage-1')
    captured_stdout = capsys.readouterr().out
    assert 'foo-bar' in captured_stdout

    mock_k8s.get_latest_pod_name.return_value = None
    _print_logs_to_stdout('the-namespace', 'bodywork-test-project--stage-1')
    captured_stdout = capsys.readouterr().out
    assert 'cannot get logs for bodywork-test-project--stage-1' in captured_stdout

    mock_k8s.get_latest_pod_name.side_effect = Exception
    _print_logs_to_stdout('the-namespace', 'bodywork-test-project--stage-1')
    captured_stdout = capsys.readouterr().out
    assert 'cannot get logs for bodywork-test-project--stage-1' in captured_stdout
