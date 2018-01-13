#!/usr/bin/env python2
# -*- coding: utf-8 -*-

"""
Loader for Israel YAML format.
See base_loader for full documentation.
"""

from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

from datetime import timedelta
import json
import os
import yaml

from cms import SCORE_MODE_MAX
from cms.db import Contest, User, Task, Statement, \
    SubmissionFormatElement, Dataset, Manager, Testcase, Attachment
from cmscontrib.loaders.base_loader import ContestLoader, TaskLoader, \
    UserLoader

from server_utils.config import CONTESTS_DIR, TASKS_DIR, USERS_FILE, \
    time_from_str
from task_utils.processing.TaskProcessor import TaskProcessor


class IsraelTaskLoader(TaskLoader):
    """
    Load a task in Israel YAML format.

    The path should be a task directory. It should contain a directory
    "auto.gen" with the generated task files, including a "module.yaml" file.

    Loading tasks outside of contests is not supported. A task is defined
    by its path, but also by its name which is provided by the contest.
    This allows more flexibility, e.g. when renaming tasks.
    """

    @staticmethod
    def detect(path):
        """
        See docstring in base_loader.

        Task detection is not supported.
        """
        raise NotImplementedError("IsraelTaskLoader doesn't "
                                  "support detection.")

    def __init__(self, path, file_cacher, task_contest_info=None):
        """
        Create a new task loader.
        task_contest_info is a dictionary containing the task info specified
        in the contest. We don't load without it.
        """
        super(IsraelTaskLoader, self).__init__(path, file_cacher)
        if task_contest_info is None:
            raise Exception("Tasks can only be loaded from a contest.")
        self.task_contest_info = task_contest_info
        self.short_name = task_contest_info["short_name"]

        self.post_gen_dir = os.path.join(path, "auto.gen")
        module_path = os.path.join(self.post_gen_dir, "module.yaml")
        self.processor = TaskProcessor(module_path, path, post_gen_dir=None)
        self.subtasks = self.processor.get_subtasks()
        self.task_type = self.processor.get_task_type()
        self.has_checker = self.processor.has_checker()
        self.has_grader = self.processor.has_grader()
        self.graders = self.processor.get_graders()
        self.headers = self.processor.get_headers()
        self.managers = self.processor.get_managers()

    def get_task(self, get_statement):
        """
        See docstring in base_loader.
        """
        args = {}

        self.put_names(args)
        if get_statement:
            self.put_statements(args)
        self.put_score_mode(args)
        self.put_task_submission_format(args)
        self.put_attachments(args)

        task = Task(**args)
        task.active_dataset = self.create_dataset(task)
        return task

    def put_names(self, args):
        """
        Put the task's short name and long name in the given args.
        """
        args["name"] = self.task_contest_info["short_name"]
        args["title"] = self.task_contest_info["long_name"]

    def put_statements(self, args):
        """
        Create Statement objects and put them in the given args.
        Define all statements as primary.
        """
        args["statements"] = []
        statements = self.processor.get_statements()

        for statement_info in statements:
            language = statement_info["language"]
            path = statement_info["path"]
            description = "Statement for task %s (lang: %s)" % \
                          (self.short_name, language)
            digest = self.file_cacher.put_file_from_path(path, description)

            args["statements"] += [Statement(language, digest)]

        languages = [statement["language"] for statement in statements]
        args["primary_statements"] = json.dumps(languages)

    def put_score_mode(self, args):
        """
        Put the score mode in the given args.
        Currently we only use the best submission (max score).
        """
        args["score_mode"] = SCORE_MODE_MAX

    def put_task_submission_format(self, args):
        """
        Put the task's submission format in the given args.
        This is just a placeholder, overridden by the dataset
        submission format.
        """
        args["submission_format"] = [SubmissionFormatElement("Task.%l")]

    def put_attachments(self, args):
        """
        Create Attachment objects and put them in the given args.
        """
        args["attachments"] = []
        attachment_paths = self.processor.get_attachments()
        for path in attachment_paths:
            base_name = os.path.basename(path)
            description = "Attachment %s for task %s" % \
                          (base_name, self.short_name)
            digest = self.file_cacher.put_file_from_path(path, description)
            args["attachments"] += [Attachment(base_name, digest)]

    def create_dataset(self, task):
        """
        Create the main dataset for this task.
        """

        args = {}
        self.put_dataset_basic_info(args, task)
        self.put_dataset_limits(args)
        self.put_dataset_score_type(args)
        self.put_dataset_type_parameters(args)
        self.put_dataset_managers(args)
        self.put_dataset_testcases(args)

        return Dataset(**args)

    def put_dataset_basic_info(self, args, task):
        """
        Put the basic dataset info in the given args:
        task, type, description, autojudge.
        """
        args["task"] = task
        args["task_type"] = self.task_type
        args["description"] = "Default"
        args["autojudge"] = False

    def put_dataset_limits(self, args):
        """
        Put the time and memory limits in the given args.
        """
        args["time_limit"] = self.processor.get_time()
        args["memory_limit"] = self.processor.get_memory()

    def put_dataset_score_type(self, args):
        """
        Put the score type parameters in the given args.
        """
        # The subtask structure is used for the score type parameters.
        # Each item in the list is of the form [score, num_testcases].
        # For example: [[10, 5], [90, 20]]
        subtask_structure = []
        for subtask in self.subtasks:
            subtask_structure += [subtask["score"], len(subtask["testcases"])]
        args["score_type_parameters"] = json.dumps(subtask_structure)

        # The score type is always "GroupMin". See CMS documentation.
        args["score_type"] = "GroupMin"

    def put_dataset_type_parameters(self, args):
        """
        Put the task type parameters in the given args.
        """

        if self.has_checker:
            comparator_str = "comparator"
        else:
            comparator_str = "diff"

        if self.has_grader:
            grader_str = "grader"
        else:
            grader_str = "alone"

        if self.task_type == "Batch":
            # Batch type expects the first parameter to be "grader" or
            # "alone"; the second parameter to have input/output file names
            # (we leave them empty to use stdin/stdout); the third parameter
            # is "comparator" or "diff".
            result = [grader_str, ["", ""], comparator_str]

        elif self.task_type in ("OutputOnly", "TwoSteps"):
            # OutputOnly and TwoSteps only expect the comparator info.
            result = [comparator_str]

        else:
            raise Exception("Unknown task type: %s" % self.task_type)

        args["task_type_parameters"] = json.dumps(result)

    def put_dataset_managers(self, args):
        """
        Put the task managers in the given args.
        Managers are all files related to the user's compilation and execution:
        checker, graders, headers, and manager.cpp (for TwoSteps).
        """
        args["managers"] = []

        for grader_path in self.graders:
            base_name = os.path.basename(grader_path)
            description = "Grader for task %s" % self.short_name
            digest = self.file_cacher.put_file_from_path(grader_path,
                                                         description)
            args["managers"] += [Manager(base_name, digest)]

        for header_path in self.headers:
            base_name = os.path.basename(header_path)
            description = "Header for task %s" % self.short_name
            digest = self.file_cacher.put_file_from_path(header_path,
                                                         description)
            args["managers"] += [Manager(base_name, digest)]

        for manager_path in self.managers:
            base_name = os.path.basename(manager_path)
            description = "Manager for task %s" % self.short_name
            digest = self.file_cacher.put_file_from_path(manager_path,
                                                         description)
            args["managers"] += [Manager(base_name, digest)]

        if self.has_checker:
            checker_path = os.path.join(self.post_gen_dir, "checker")
            description = "Manager for task %s" % self.short_name
            digest = self.file_cacher.put_file_from_path(checker_path,
                                                         description)
            args["managers"] += [Manager("checker", digest)]

    def put_dataset_testcases(self, args):
        """
        Put the task's testcases in the given args.
        """
        args["testcases"] = []

        total_testcase_index = 0
        for (subtask_index, subtask) in enumerate(self.subtasks):
            for (testcase_index, testcase) in enumerate(subtask["testcases"]):
                input_path = testcase["input"]
                output_path = testcase["output"]

                input_desc = "Input %02d.%02d for task %s" % \
                             (subtask_index, testcase_index, self.short_name)
                output_desc = "Output %02d.%02d for task %s" % \
                              (subtask_index, testcase_index, self.short_name)

                input_digest = self.file_cacher.put_file_from_path(
                    input_path, input_desc)
                output_digest = self.file_cacher.put_file_from_path(
                    output_path, output_desc)

                codename = "%03d" % total_testcase_index

                args["testcases"] += [
                    Testcase(codename, True, input_digest, output_digest)
                ]

                total_testcase_index += 1

    def task_has_changed(self):
        """
        See docstring in base_loader.
        """
        return True


class IsraelUserLoader(UserLoader):
    """
    See docstring in base_loader.

    We assume all users are in the global users file, see config.yaml.
    We take loading a "path" to mean loading a username.
    """

    @staticmethod
    def detect(path):
        """
        See docstring in base_loader.

        We abuse the path argument to mean username.
        """
        username = path
        return IsraelUserLoader._get_user_info(username) is not None

    @staticmethod
    def _get_user_info(username):
        """
        Get info about the given user from the global users file.
        If not a valid user, return None.

        If it is a valid user, return a dictionary with keys:
        username, password, first_name, last_name.
        Other keys in the users file are ignored.
        """

        with open(USERS_FILE) as stream:
            users_list = yaml.safe_load(stream)

        for user_dict in users_list:
            if user_dict["username"] == username:
                return {
                    "username": username,
                    "password": user_dict["password"],
                    "first_name": user_dict["first_name"],
                    "last_name": user_dict["last_name"]
                }
        return None

    def __init__(self, path, file_cacher):
        """
        See docstring in base_loader.

        We abuse the path argument to mean username.
        """
        super(IsraelUserLoader, self).__init__(path, file_cacher)
        self.username = path
        self.user_info = IsraelUserLoader._get_user_info(self.username)

    def get_user(self):
        """
        See docstring in base_loader.
        """
        return User(**self.user_info)

    def user_has_changed(self):
        """
        See docstring in base_loader.
        """
        return True

    def get_task_loader(self, taskname):
        """
        This method is only implemented in the contest loader,
        it is here for linting, because BaseLoader defines it as abstract.
        """
        raise NotImplementedError("IsraelUserLoader does not provide "
                                  "get_task_loader")


class IsraelContestLoader(ContestLoader):
    """
    Load a contest in Israel YAML format.
    """

    short_name = "israel_contest"
    description = "Israel YAML contest format"

    @staticmethod
    def detect(path):
        """
        See docstring in base_loader.

        A contest path is valid if it contains "module.yaml".
        The path is first checked by itself, and if not valid,
        it is checked inside the contests directory.
        """

        # Check the given path.
        module_path = os.path.join(path, "module.yaml")
        if os.path.isfile(module_path):
            return True

        # Check the given path inside contests directory.
        module_path = os.path.join(CONTESTS_DIR, module_path)
        return os.path.isfile(module_path)

    def __init__(self, path, file_cacher):
        """
        See docstring in base_loader.

        The contest path should contain a "module.yaml" file (see templates).
        """
        super(IsraelContestLoader, self).__init__(path, file_cacher)

        # Get the module from the given path, inside the contests directory
        # if needed.
        module_path = os.path.join(path, "module.yaml")
        if not os.path.isfile(module_path):
            module_path = os.path.join(CONTESTS_DIR, module_path)

        self.contest_dir = os.path.dirname(os.path.abspath(module_path))

        with open(module_path) as stream:
            self.params = yaml.safe_load(stream)

    def get_task_loader(self, taskname):
        task_info = self.params["tasks"][taskname]
        task_path = os.path.join(TASKS_DIR, task_info["path"])
        return IsraelTaskLoader(task_path, self.file_cacher, task_info)

    def get_contest(self):
        """
        See docstring in base_loader.
        """
        contest = self.get_contest_object()
        participations = IsraelContestLoader.get_participations_info()
        tasks = self.get_tasks_list()
        return contest, tasks, participations

    def get_contest_object(self):
        """
        Return the Contest database object.
        """
        args = {}

        # Names.
        args["name"] = self.params["short_name"]
        args["description"] = self.params["long_name"]

        # Languages.
        args["languages"] = self.params["languages"]

        # Times.
        args["start"] = time_from_str(self.params["start_time"])
        args["stop"] = time_from_str(self.params["end_time"])

        # Limits.
        args["max_submission_number"] = self.params["max_submission_number"]
        args["max_user_test_number"] = self.params["max_user_test_number"]

        interval_seconds = self.params["min_submission_interval"]
        delta = timedelta(seconds=interval_seconds)
        args["min_submission_interval"] = delta

        interval_seconds = self.params["min_user_test_interval"]
        delta = timedelta(seconds=interval_seconds)
        args["min_user_test_interval"] = delta

        return Contest(**args)

    def get_tasks_list(self):
        """
        Return a list of this contest's tasks' short names.
        """
        return [task["short_name"] for task in self.params["tasks"]]

    def contest_has_changed(self):
        """
        See docstring in base_loader.
        """
        raise NotImplementedError("Please extend ContestLoader")

    @staticmethod
    def get_participations_info():
        """
        To create a participation, we need two fields:
        a username, and whether the user is hidden.

        Passwords are ignored, since we don't use contest-specific passwords.
        """
        with open(USERS_FILE) as stream:
            users_list = yaml.safe_load(stream)

        result = []
        for user in users_list:
            participation_info = {"username": user["username"]}
            if "hidden" in user:
                participation_info["hidden"] = user["hidden"]
            result += [participation_info]
        return result