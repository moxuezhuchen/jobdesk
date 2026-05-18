"""测试 core/template.py - 命令模板渲染。"""

import pytest
from jobdesk_app.core.template import render_command


class TestRenderCommand:
    def test_simple_variable(self):
        result = render_command(
            "g16 {input_name}",
            {"input_name": "mol_001.gjf"},
        )
        assert result == "g16 mol_001.gjf"

    def test_multiple_variables(self):
        result = render_command(
            "cd {job_dir} && g16 {input_name}",
            {
                "job_dir": "/remote/batch/t1",
                "input_name": "mol_001.gjf",
            },
        )
        assert result == "cd /remote/batch/t1 && g16 mol_001.gjf"

    def test_stem_variable(self):
        result = render_command(
            "orca {input_name} > {stem}.out",
            {"input_name": "mol_001.inp"},
        )
        assert result == "orca mol_001.inp > mol_001.out"

    def test_stem_explicit(self):
        result = render_command(
            "echo {stem}",
            {"stem": "custom_stem"},
        )
        assert result == "echo custom_stem"

    def test_all_variables(self):
        result = render_command(
            "{batch_id}/{task_id}/{job_dir}/{input_file}/{input_name}/{stem}",
            {
                "batch_id": "b001",
                "task_id": "t001",
                "job_dir": "/remote/job",
                "input_file": "/path/to/input.gjf",
                "input_name": "input.gjf",
                "stem": "input",
            },
        )
        assert result == "b001/t001//remote/job//path/to/input.gjf/input.gjf/input"

    def test_missing_variable_raises(self):
        with pytest.raises(ValueError, match="需要变量"):
            render_command("g16 {input_name}", {})

    def test_missing_variable_raises_clear_message(self):
        with pytest.raises(ValueError) as exc_info:
            render_command("cd {job_dir} && g16 {input_name}", {"input_name": "t.gjf"})
        assert "job_dir" in str(exc_info.value)

    def test_no_variables(self):
        result = render_command("bash run.sh", {})
        assert result == "bash run.sh"

    def test_unknown_variable_raises(self):
        with pytest.raises(ValueError, match="不支持的变量"):
            render_command("echo {unknown_var}", {"unknown_var": "x"})

    def test_partial_variables(self):
        result = render_command(
            "ls {job_dir}",
            {"job_dir": "/remote"},
        )
        assert result == "ls /remote"

    def test_task_id_only(self):
        result = render_command(
            "echo {task_id}",
            {"task_id": "abc123"},
        )
        assert result == "echo abc123"

    def test_shell_sensitive_filename_variables_are_quoted(self):
        result = render_command(
            "g16 {input_name} > {entry_stem}.log",
            {"input_name": "mol 1;rm.gjf", "entry_stem": "mol 1;rm"},
        )
        assert result == "g16 'mol 1;rm.gjf' > 'mol 1;rm'.log"
