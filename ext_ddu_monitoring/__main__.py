from dynatrace_extension import Extension, Status, StatusValue
import requests, traceback
from datetime import datetime, timezone, timedelta

class ExtensionImpl(Extension):

    def query(self):
        """
        The query method is automatically scheduled to run every minute
        """
        self.logger.info("Query method started for ext_ddu_monitoring.")

        for endpoint in self.activation_config["endpoints"]:

            # Dynatrace Environment URL | Managed: https://{your-domain}/e/{your-environment-id} | SaaS: https://{your-environment-id}.live.dynatrace.com
            environment_url = endpoint["environment_url"]

            # API Token with following permissions: 
            # - Read problems
            # - Write problems
            # - Read metrics
            # - Read entities
            # - Read extensions
            # - Read extension monitoring configurations
            api_token = endpoint["api_token"]

            # Text for fetching problems by problem text
            problem_text = endpoint["problem_text"]

            # Threshold for datapoint increase to define which extensions should be considered for analysis
            datapoint_delta_threshold = endpoint["datapoint_delta_threshold"]

            # Enable/disable verify SSL certificate for API requests
            verify_ssl = endpoint["verify_ssl"]

            # ================================================================================================
            # ================================================================================================

            # Monitor extension DDU consumption related problems
            self.monitor_ddu_problems(environment_url, api_token, problem_text, datapoint_delta_threshold, verify_ssl)

            # ================================================================================================
            # ================================================================================================

        self.logger.info("Query method ended for ext_ddu_monitoring.")

    def fastcheck(self) -> Status:
        """
        This is called when the extension runs for the first time.
        If this AG cannot run this extension, raise an Exception or return StatusValue.ERROR!
        """
        return Status(StatusValue.OK)
    
    def monitor_ddu_problems(self, environment_url, api_token, problem_text, datapoint_delta_threshold, verify_ssl):

        # Class for mapping delta of currently and previously ingested data points for an extension configuration
        class ExtensionConsumption:

            # constructor function    
            def __init__(self, extension_name = "", config_id = "", current_datapoints = 0, previous_datapoints = 0):
                self.extension_name = extension_name
                self.config_id = config_id
                self.current_datapoints = current_datapoints
                self.previous_datapoints = previous_datapoints
                self.affected_entities = []

            def delta(self):
                return self.current_datapoints - self.previous_datapoints

        try:
            self.logger.info(f"Analyzing DDU problems for endpoint {environment_url} with problem text: {problem_text}.")

            datetime_to = datetime.now(timezone.utc)
            datetime_from = datetime_to - timedelta(minutes=5)

            time_to = datetime_to.isoformat(timespec='milliseconds')
            time_from = datetime_from.isoformat(timespec='milliseconds')

            # Fetch DDU monitoring alert problems
            # =================================================================================
            problems_api = environment_url + "/api/v2/problems"
            problem_selector = f"status(open),text({problem_text})"

            problem_datetime_from = datetime_to - timedelta(minutes=1)
            problem_time_from = problem_datetime_from.isoformat(timespec='milliseconds')
            problem_time_to = time_to

            params = {
                "api-token": api_token, 
                "pageSize": 10,
                "from": problem_time_from, 
                "to": problem_time_to, 
                "problemSelector": problem_selector,
                "fields": "recentComments"
            }
            response = requests.get(problems_api, params, verify=verify_ssl)
            problems = response.json()["problems"]

            self.logger.info(f"Number of detected problems for analysis: {len(problems)}.")

            # Start root cause analysis of DDU spike for each problem
            # =================================================================================
            for problem in problems:
                
                # Check if problem has already been analyzed and commented
                if problem["recentComments"]["totalCount"] == 0:

                    self.logger.info(f"Analyzing problem with ID: {problem['problemId']}.")
                    
                    # Get ingested metric data points split by extension and config for current period
                    # =================================================================================
                    metrics_query_api = environment_url + "/api/v2/metrics/query"
                    metric_selector = "dsfm:server.metrics.ingest.external_datapoints:splitBy(source,\"dt.extension.config.id\"):sort(value(auto,descending)):fold(sum)"

                    # Check last 5 min
                    params = {
                        "api-token": api_token, 
                        "metricSelector": metric_selector,
                        "from": time_from,
                        "to": time_to,
                        "pageSize": 10000
                    }
                    response = requests.get(metrics_query_api, params, verify=verify_ssl)
                    metric_resultlist = response.json()["result"][0]["data"]

                    # Get ingested metric data points split by extension and config for previous period
                    # =================================================================================
                    datetime_from_shifted = datetime_from - timedelta(hours=1)
                    datetime_to_shifted = datetime_to - timedelta(hours=1)

                    time_from_shifted = datetime_from_shifted.isoformat(timespec='milliseconds')
                    time_to_shifted = datetime_to_shifted.isoformat(timespec='milliseconds')

                    # For comparison shift the 5 min period 1 hour earlier
                    params = {
                        "api-token": api_token, 
                        "metricSelector": metric_selector,
                        "from": time_from_shifted,
                        "to": time_to_shifted,
                        "pageSize": 10000
                    }
                    response = requests.get(metrics_query_api, params, verify=verify_ssl)
                    shifted_metric_resultlist = response.json()["result"][0]["data"]

                    # Get list of extension names
                    # =================================================================================
                    extensions_api = environment_url + "/api/v2/extensions"

                    params = {
                        "api-token": api_token, 
                        "pageSize": 100
                    }
                    response = requests.get(extensions_api, params, verify=verify_ssl)
                    extension_list = response.json()["extensions"]

                    extension_names = []
                    for ext in extension_list:
                        extension_names.append(ext["extensionName"])

                    # Collect data points per extension config for current period
                    # =================================================================================

                    # Dictionary to hold consumption data for each extension
                    extension_dict = {}

                    for metric_data in metric_resultlist:
                        
                        source = metric_data["dimensionMap"]["source"] # source contains extension name
                        extension_config_id = metric_data["dimensionMap"]["dt.extension.config.id"]
                        
                        if source in extension_names: # if source is an extension
                            key = source + "|" + extension_config_id
                            value = metric_data["values"][0]

                            if key in extension_dict:
                                extension_dict[key].current_datapoints += value
                            else:
                                extension_dict[key] = ExtensionConsumption(source, extension_config_id, value, 0)

                    # Collect data points per extension config for previous period
                    # =================================================================================

                    for metric_data in shifted_metric_resultlist:
                        
                        source = metric_data["dimensionMap"]["source"] # source contains extension name
                        extension_config_id = metric_data["dimensionMap"]["dt.extension.config.id"]
                        
                        if source in extension_names: # if source is an extension
                            key = source + "|" + extension_config_id
                            value = metric_data["values"][0]

                            if key in extension_dict:
                                extension_dict[key].previous_datapoints += value
                            else:
                                extension_dict[key] = ExtensionConsumption(source, extension_config_id, 0, value)

                    # Collect extensions where delta exceeds the defined threshold
                    # =================================================================================
                    extensions = []
                    
                    for key in extension_dict.keys():
                        extension = extension_dict[key]
                        if extension.delta() > datapoint_delta_threshold:
                            extensions.append(extension)

                    self.logger.info(f"Found {len(extensions)} extensions which exceed the specified threshold of {datapoint_delta_threshold} for datapoints increase.")

                    # Create dictionary of billed DDUs per host entity
                    # =================================================================================
                    ddu_by_host_dict = {}
                    ddu_by_hostselector = "builtin:billing.ddu.metrics.byEntity:filter(in(\"dt.entity.monitored_entity\", entitySelector(\"type(~\"HOST~\")\"))):splitBy(\"dt.entity.monitored_entity\"):sort(value(auto,descending)):fold(sum)"
                        
                    # Collect DDUs per host entity for current timeframe
                    params = {
                        "api-token": api_token, 
                        "metricSelector": ddu_by_hostselector,
                        "from": time_from,
                        "to": time_to,
                        "pageSize": 10000
                    }
                    response = requests.get(metrics_query_api, params, verify=verify_ssl)
                    metric_resultlist = response.json()["result"][0]["data"]

                    # Summarize DDUs per host entity for current period
                    for metric_data in metric_resultlist:
                        
                        entity_id = metric_data.dimensionMap['dt.entity.monitored_entity']
                        billed_ddus = metric_data.values[0]
                        
                        if entity_id not in ddu_by_host_dict:
                            ddu_by_host_dict[entity_id] = {
                                "current_billed_ddus": 0,
                                "previous_billed_ddus": 0
                            }

                        ddu_by_host_dict[entity_id].current_billed_ddus += billed_ddus

                    # Collect DDUs per host entity for previous timeframe
                    params = {
                        "api-token": api_token, 
                        "metricSelector": ddu_by_hostselector,
                        "from": time_from_shifted,
                        "to": time_to_shifted,
                        "pageSize": 10000
                    }
                    response = requests.get(metrics_query_api, params, verify=verify_ssl)
                    metric_resultlist = response.json()["result"][0]["data"]

                    # Summarize DDUs per host entity for previous period
                    for metric_data in metric_resultlist:
                        
                        entity_id = metric_data.dimensionMap['dt.entity.monitored_entity']
                        billed_ddus = metric_data.values[0]
                        
                        if entity_id not in ddu_by_host_dict:
                            ddu_by_host_dict[entity_id] = {
                                "current_billed_ddus": 0,
                                "previous_billed_ddus": 0
                            }

                        ddu_by_host_dict[entity_id].previous_billed_ddus += billed_ddus

                    # Determine extensions where data point increase was billable
                    # =================================================================================
                    bill_affecting_extensions = []
                    monitored_entities_api = environment_url + "/api/v2/entities"

                    for ext in extensions:
                        # Get scope of extension monitoring configuration
                        monitoring_configuration_api = environment_url + f"/api/v2/extensions/{ext.extension_name}/monitoringConfigurations/{ext.config_id}"

                        params = {
                            "api-token": api_token, 
                        }
                        response = requests.get(monitoring_configuration_api, params, verify=verify_ssl)
                        configDetails = response.json()
                        scope = configDetails["scope"]
                        
                        # Check if OneAgent extension
                        if scope.startswith("HOST") or scope.startswith("management_zone"):
                            
                            # Collect host entity Ids of extension scope
                            entity_ids = []

                            if scope.startswith("HOST_GROUP"):
                                
                                hostgroup_selector = f"type(HOST),fromRelationships.isInstanceOf(entityId({scope}))"
                                params = {
                                    "api-token": api_token, 
                                    "pageSize": 500,
                                    "from": time_from_shifted,
                                    "to": time_to,
                                    "entitySelector": hostgroup_selector,
                                }
                                response = requests.get(monitored_entities_api, params, verify=verify_ssl)
                                entities = response.json()["entities"]

                                host_ids = []
                                for host in entities:
                                    host_ids.append(host["entityId"])
                                
                                entity_ids.push(host_ids)

                            elif scope.startswith("HOST"):
                                entity_ids.append(scope)

                            elif scope.startswith("management_zone"):

                                mgmt_zone_name = scope.substring(16); # removing 'management_zone-'
                                mgmt_zone_selector = f"type(HOST),mzName({mgmt_zone_name}))"
                                
                                params = {
                                    "api-token": api_token, 
                                    "pageSize": 500,
                                    "from": time_from_shifted,
                                    "to": time_to,
                                    "entitySelector": mgmt_zone_selector,
                                }
                                response = requests.get(monitored_entities_api, params, verify=verify_ssl)
                                entities = response.json()["entities"]

                                host_ids = []
                                for host in entities:
                                    host_ids.append(host["entityId"])
                                
                                entity_ids.push(host_ids)

                            # Check for each entity if DDU consumption was covered by free tier
                            for entity_id in entity_ids:
                                
                                if entity_id in ddu_by_host_dict:
                                    
                                    entity = ddu_by_host_dict[entity_id]
                                    
                                    # Check if there was an increase of billable DDUs
                                    delta = entity["current_billed_ddus"] - entity["previous_billed_ddus"]
                                    if delta > 0:
                                        ext.affected_entities.append(entity_id)
                                        bill_affecting_extensions.append(ext)

                        else:
                            # ActiveGate extensions are assumed to be billable
                            bill_affecting_extensions.append(ext)

                    # Post comment to problem with analysis result
                    # =================================================================================
                    
                    problem_id = problem["problemId"]
                    problem_comment_api = environment_url + f"/api/v2/problems/{problem_id}/comments"

                    if len(bill_affecting_extensions) > 0:
                        
                        self.logger.info(f"Detected {len(bill_affecting_extensions)} bill-affecting extensions.")

                        message = "DDU root cause analysis: \n"
                        
                        for ext in bill_affecting_extensions:
                            message += f"Extension: {ext.extension_name} \nConfig ID: {ext.config_id} \nData point increase: {ext.delta()} \nAffected Entities: {', '.join(ext.affected_entities)} \n====================\n"
                    
                        params = {
                            "api-token": api_token, 
                            "message": message
                        }
                        requests.post(problem_comment_api, params, verify=verify_ssl)

                    else:
                        self.logger.info("No bill-affecting extensions were detected.")
                        
                        message = "DDU root cause analysis: \nNo bill-affecting extensions were detected."
                        params = {
                            "api-token": api_token, 
                            "message": message
                        }
                        requests.post(problem_comment_api, params, verify=verify_ssl)
                        
                    self.logger.info(f"Added comment with analysis result to problem {problem_id}.")

                else:
                    self.logger.info(f"Problem with ID {problem['problemId']} has already been analyzed.")

            self.logger.info(f"Finished analysis of DDU problems.")    

        except:
            self.logger.error("ERROR WHILE MONITORING DDU PROBLEMS")
            self.logger.error(traceback.format_exc())

def main():
    ExtensionImpl(name="ext_ddu_monitoring").run()



if __name__ == '__main__':
    main()
