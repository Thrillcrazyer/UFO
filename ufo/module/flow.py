# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import logging
import os
import time
import json

from art import text2art
from pywinauto.uia_defines import NoPatternInterfaceError
from ..experience.summarizer import ExperienceSummarizer

from ..rag import retriever_factory
from ..config.config import load_config
from ..llm import llm_call
from ..prompter.agent_prompter import ApplicationAgentPrompter, ActionAgentPrompter
from ..ui_control import control, screenshot as screen ## 수정사항 1 => UI Control 하는거 로봇 Control 로 변경해야 함
from ..ui_control.executor import ActionExecutor
from ..utils import (create_folder, encode_image_from_path,
                     generate_function_call, json_parser, print_with_color,
                     revise_line_breaks, yes_or_no)
from ..robot_control import robot

configs = load_config()
BACKEND = configs["CONTROL_BACKEND"]



class Session(object):
    """
    A session for UFO.
    """
    def __init__(self, task):
        """
        Initialize a session.
        :param task: The name of current task.
        :param gpt_key: GPT key.
        """
        self.task = task
        self.step = 0
        self.round = 0
        self.action_history = []

        self.log_path = f"logs/{self.task}/"
        create_folder(self.log_path)
        self.logger = self.initialize_logger(self.log_path, "response.log")
        self.request_logger = self.initialize_logger(self.log_path, "request.log")

        self.app_selection_prompter = ApplicationAgentPrompter(configs["APP_AGENT"]["VISUAL_MODE"], configs["APP_SELECTION_PROMPT"], configs["APP_SELECTION_EXAMPLE_PROMPT"], configs["API_PROMPT"])
        self.act_selection_prompter = ActionAgentPrompter(configs["ACTION_AGENT"]["VISUAL_MODE"], configs["ACTION_SELECTION_PROMPT"], configs["ACTION_SELECTION_EXAMPLE_PROMPT"], configs["API_PROMPT"])

        self.status = "APP_SELECTION"
        self.application = ""
        self.app_root = ""
        self.app_window = None
        self.plan = ""
        self.request = ""
        self.results = ""
        self.cost = 0
        self.offline_doc_retriever = None
        self.online_doc_retriever = None
        self.experience_retriever = None
        self.control_reannotate = None
        
        # 추가한 로봇 API code
        self.robot=robot.robot_api(port=5000)

        welcome_text = """
Welcome to use CA Robo🛸, A Decision Maker for Robot Control System using LLM. 
{art}
Please enter your request to be completed🛸: """.format(art=text2art("CA ROBO"))

        print_with_color(welcome_text, "cyan")
        
        self.request = input()
        self.request_history = []

    def process_application_selection(self):

        """
        Select an action.
        header: The headers of the request.
        return: The outcome, the application window, and the action log.
        """
        
        # Code for selecting an action
        print_with_color("Step {step}: Selecting an application.".format(step=self.step), "magenta")
        robot_view_save_path = self.log_path + f"action_step{self.step}.png"
        _ = self.robot.capture_frame(robot_view_save_path) ## 이부분 수정해야 함. 로봇에서 데이터 가져오는 느낌으로 ㅇㅇ
        distance=self.robot.get_distance()
        robot_view = encode_image_from_path(robot_view_save_path)

        self.results = ""
        

        app_selection_prompt_system_message = self.app_selection_prompter.system_prompt_construction() ## 로봇으로 변경
        app_selection_prompt_user_message = self.app_selection_prompter.user_content_construction([robot_view], self.request_history, self.action_history, 
                                                                                                  self.plan, self.request,distance) ## 로봇으로 변경 
        
        app_selection_prompt_message = self.app_selection_prompter.prompt_construction(app_selection_prompt_system_message, app_selection_prompt_user_message)
        
        
        self.request_logger.debug(json.dumps({"step": self.step, "prompt": app_selection_prompt_message, "status": ""}))

        try:
            response_string, cost = llm_call.get_completion(app_selection_prompt_message, "APP", use_backup_engine=True)

        except Exception as e:
            log = json.dumps({"step": self.step, "status": str(e), "prompt": app_selection_prompt_message})
            print_with_color("Error occurs when calling LLM: {e}".format(e=str(e)), "red")
            self.request_logger.info(log)
            self.status = "ERROR"
            return

        self.cost += cost

        try:
            response_json = json_parser(response_string)

            observation = response_json["Observation"]
            thought = response_json["Thought"]
            self.plan = response_json["Plan"]
            self.status = response_json["Status"]
            comment = response_json["Comment"]

            print_with_color("Observations👀: {observation}".format(observation=observation), "cyan")
            print_with_color("Thoughts💡: {thought}".format(thought=thought), "green")
            print_with_color("Next Plan📚: {plan}".format(plan=str(self.plan).replace("\\n", "\n")), "cyan")
            print_with_color("Comment💬: {comment}".format(comment=comment), "green")
            
            
            response_json["Step"] = self.step
            response_json["Round"] = self.round
            response_json["Action"] = "set_focus()"
            response_json["Request"] = self.request
            response_json["Agent"] = "AppAgent"
        
            if self.status.upper()=="FINISH":
                self.status = "FINISH"
                response_json["Application"] = ""
                response_json = self.set_result_and_log("", response_json)
                return
            
    
            response_json = self.set_result_and_log("", response_json)

            
            self.status = "CONTINUE"

            # Initialize the document retriever
            if configs["RAG_OFFLINE_DOCS"]:
                print_with_color("Loading offline document indexer for {app}...".format(app=self.application), "magenta")
                self.offline_doc_retriever = retriever_factory.OfflineDocRetriever(self.application)
            if configs["RAG_ONLINE_SEARCH"]:
                print_with_color("Creating a Bing search indexer...", "magenta")
                self.online_doc_retriever = retriever_factory.OnlineDocRetriever(self.request)
            if configs["RAG_EXPERIENCE"]:
                print_with_color("Creating an experience indexer...", "magenta")
                experience_path = configs["EXPERIENCE_SAVED_PATH"]
                db_path = os.path.join(experience_path, "experience_db")
                self.experience_retriever = retriever_factory.ExperienceRetriever(db_path)


            time.sleep(configs["SLEEP_TIME"])

            self.step += 1

        except Exception as e:
            print_with_color("Error Occurs at application selection.", "red")
            print_with_color(str(e), "red")
            print_with_color(response_string, "red")
            self.error_logger(response_string, str(e))
            self.status = "ERROR"

            return


    def process_action_selection(self):
        """
        Select an action.
        header: The headers of the request.
        return: The outcome, the application window, and the action log.
        """

        print_with_color("Step {step}: Taking an action on application {application}.".format(step=self.step, application=self.application), "magenta")
        robot_view_save_path = self.log_path + f"action_step{self.step}.png"
        annotated_robot_view_save_path = self.log_path + f"action_step{self.step}_annotated.png"
        concat_robot_view_save_path = self.log_path + f"action_step{self.step}_concat.png"
        _ = self.robot.capture_frame(robot_view_save_path) ## 이부분 수정해야 함. 로봇에서 데이터 가져오는 느낌으로 ㅇㅇ
        distance=self.robot.get_distance()
        image_url = []

            
        last_screenshot_save_path = self.log_path + f"action_step{self.step - 1}.png"
        last_control_screenshot_save_path = self.log_path + f"action_step{self.step - 1}_selected_controls.png"
        image_url += [encode_image_from_path(last_control_screenshot_save_path if os.path.exists(last_control_screenshot_save_path) else last_screenshot_save_path)]

        

        if configs["RAG_EXPERIENCE"]:
            examples, tips = self.rag_experience_retrieve()
        else:
            examples = []
            tips = []

        action_selection_prompt_system_message = self.act_selection_prompter.system_prompt_construction(examples, tips) # 수정해야 하는 사항들
        action_selection_prompt_user_message = self.act_selection_prompter.user_content_construction(image_url, self.request_history, self.action_history, 
                                                                                                     self.plan, self.request, self.rag_prompt(), configs["INCLUDE_LAST_SCREENSHOT"]) # 이 함수도 수정해야 함.
        
        action_selection_prompt_message = self.act_selection_prompter.prompt_construction(action_selection_prompt_system_message, action_selection_prompt_user_message)

        self.request_logger.debug(json.dumps({"step": self.step, "prompt": action_selection_prompt_message, "status": ""}))

        try:
            response_string, cost = llm_call.get_completion(action_selection_prompt_message, "ACTION", use_backup_engine=True)
        except Exception as e:
            log = json.dumps({"step": self.step, "status": str(e), "prompt": action_selection_prompt_message})
            print_with_color("Error occurs when calling LLM: {e}".format(e=str(e)), "red")
            self.request_logger.info(log)
            self.status = "ERROR"
            time.sleep(configs["SLEEP_TIME"])
            return 
        
        self.cost += cost

        try:
            response_json = json_parser(response_string)
            observation = response_json["Observation"]
            thought = response_json["Thought"]
            function_call = response_json["Function"]
            args = revise_line_breaks(response_json["Args"])

            # Compose the function call and the arguments string.
            action = generate_function_call(function_call, args) # 이 코드도 수정해야 함.
            
            self.robot.get_data(action)

            # Set the result and log the result.
            self.plan = response_json["Plan"]
            self.status = response_json["Status"]
            comment = response_json["Comment"]
            response_json["Step"] = self.step
            response_json["Round"] = self.round
            response_json["Action"] = action
            response_json["Agent"] = "ActAgent"
            response_json["Request"] = self.request
            response_json["Application"] = self.app_root
            

            # Log the response.
            print_with_color("Observations👀: {observation}".format(observation=observation), "cyan")
            print_with_color("Thoughts💡: {thought}".format(thought=thought), "green")
            print_with_color("Action applied⚒️: {action}".format(action=action), "blue")
            print_with_color("Status📊: {status}".format(status=self.status), "blue")
            print_with_color("Next Plan📚: {plan}".format(plan=str(self.plan).replace("\\n", "\n")), "cyan")
            print_with_color("Comment💬: {comment}".format(comment=comment), "green")
            


        except Exception as e:
            # Return the error message and log the error.
            print_with_color("Error occurs at step {step}".format(step=self.step), "red")
            print_with_color(str(e), "red")
            self.status = "ERROR"
            self.error_logger(response_string, str(e))
            
            return
        
        self.step += 1

        self.control_reannotate = None
            
        # The task is finished and no further action is needed
        if self.status.upper() == "FINISH":
            self.status = self.status.upper()
            response_json = self.set_result_and_log("", response_json)
            
            return
        
        # Execute the action
        results = f"{function_call}, {args} ard used"
        response_json = self.set_result_and_log(results, response_json)
        time.sleep(configs["SLEEP_TIME"])

        return
    

    def rag_prompt(self):
        """
        Retrieving documents for the user request.
        :return: The retrieved documents string.
        """
        
        retrieved_docs = ""
        if self.offline_doc_retriever:
            offline_docs = self.offline_doc_retriever.retrieve("How to {query} for {app}".format(query=self.request, app=self.application), configs["RAG_OFFLINE_DOCS_RETRIEVED_TOPK"], filter=None)
            offline_docs_prompt = self.act_selection_prompter.retrived_documents_prompt_helper("Help Documents", "Document", [doc.metadata["text"] for doc in offline_docs])
            retrieved_docs += offline_docs_prompt

        if self.online_doc_retriever:
            online_search_docs = self.online_doc_retriever.retrieve(self.request, configs["RAG_ONLINE_RETRIEVED_TOPK"], filter=None)
            online_docs_prompt = self.act_selection_prompter.retrived_documents_prompt_helper("Online Search Results", "Search Result", [doc.page_content for doc in online_search_docs])
            retrieved_docs += online_docs_prompt

        return retrieved_docs

    
    def rag_experience_retrieve(self):
        """
        Retrieving experience examples for the user request.
        :return: The retrieved examples and tips string.
        """
        
        # Retrieve experience examples. Only retrieve the examples that are related to the current application.
        experience_docs = self.experience_retriever.retrieve(self.request, configs["RAG_EXPERIENCE_RETRIEVED_TOPK"], 
                                                                filter=lambda x: self.app_root.lower() in [app.lower() for app in x["app_list"]])
        
        if experience_docs:
            examples = [doc.metadata.get("example", {}) for doc in experience_docs]
            tips = [doc.metadata.get("Tips", "") for doc in experience_docs]
        else:
            examples = []
            tips = []

        return examples, tips

    def experience_asker(self):
        print_with_color("""Would you like to save the current conversation flow for future reference by the agent?
[Y] for yes, any other key for no.""", "cyan")
        
        self.request = input()

        if self.request.upper() == "Y":
            return True
        else:
            return False
        

    def experience_saver(self):
        """
        Save the current agent experience.
        """
        print_with_color("Summarizing and saving the execution flow as experience...", "yellow")

        summarizer = ExperienceSummarizer(configs["ACTION_AGENT"]["VISUAL_MODE"], configs["EXPERIENCE_PROMPT"], configs["ACTION_SELECTION_EXAMPLE_PROMPT"], configs["API_PROMPT"])
        experience = summarizer.read_logs(self.log_path)
        summaries, total_cost = summarizer.get_summary_list(experience)

        experience_path = configs["EXPERIENCE_SAVED_PATH"]
        create_folder(experience_path)
        summarizer.create_or_update_yaml(summaries, os.path.join(experience_path, "experience.yaml"))
        summarizer.create_or_update_vector_db(summaries, os.path.join(experience_path, "experience_db"))

        self.cost += total_cost
        print_with_color("The experience has been saved.", "cyan")


    def set_new_round(self):
        """
        Start a new round.
        """
        self.request_history.append({self.round: self.request})
        self.round += 1
        print_with_color("""Please enter your new request. Enter 'N' for exit.""", "cyan")
        
        self.request = input()

        if self.request.upper() == "N":
            self.status = "ALLFINISH"
            return
        else:
            self.status = "APP_SELECTION"
            return
        
    def get_round(self):
        """
        Get the round of the session.
        return: The round of the session.
        """
        return self.round
    
    def set_round(self, new_round):
        """
        Set the round of the session.
        """
        self.round = new_round


    def get_status(self):
        """
        Get the status of the session.
        return: The status of the session.
        """
        return self.status
    
    def get_step(self):
        """
        Get the step of the session.
        return: The step of the session.
        """
        return self.step

    def get_results(self):
        """
        Get the results of the session.
        return: The results of the session.
        """
        return self.results
    
    def get_cost(self):
        """
        Get the cost of the session.
        return: The cost of the session.
        """
        return self.cost
    
    def get_application_window(self):
        """
        Get the application of the session.
        return: The application of the session.
        """
        return self.app_window
    
    def set_result_and_log(self, result, response_json):
        """
        Set the result of the session, and log the result.
        """
        if type(result) != str and type(result) != list:
            result = ""
        response_json["Results"] = result
        #self.logger.info(json.dumps(response_json))
        self.action_history.append({key: response_json[key] for key in configs["HISTORY_KEYS"]})
        self.results = result

        return response_json
    
    def safe_guard(self, action, control_text):
        """
        Safe guard for the session.
        """
        if "PENDING" in self.status.upper() and configs["SAFE_GUARD"]:
            print_with_color("[Input Required:] UFO🛸 will apply {action} on the [{control_text}] item. Please confirm whether to proceed or not. Please input Y or N.".format(action=action, control_text=control_text), "magenta")
            decision = yes_or_no()
            if not decision:
                print_with_color("The user decide to stop the task.", "magenta")
                self.status = "FINISH"
                return False
            
            # Handle the PENDING_AND_FINISH case
            elif "FINISH" in self.plan:
                self.status = "FINISH"
        return True
    
    def error_logger(self, response_str, error):
        """
        Error handler for the session.
        """
        log = json.dumps({"step": self.step, "status": "ERROR", "response": response_str, "error": error})
        self.logger.info(log)

    @staticmethod
    def initialize_logger(log_path, log_filename):
        """
        Initialize logging.
        log_path: The path of the log file.
        log_filename: The name of the log file.
        return: The logger.
        """
        # Code for initializing logging
        logger = logging.Logger(log_filename)

        if not configs["PRINT_LOG"]:
            # Remove existing handlers if PRINT_LOG is False
            logger.handlers = []


        log_file_path = os.path.join(log_path, log_filename)
        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        formatter = logging.Formatter('%(message)s')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.setLevel(configs["LOG_LEVEL"])

        return logger