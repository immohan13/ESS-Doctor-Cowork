## Topic generator

| Topic | Backend | Agent type | Does | ESS scenario |
| --- | --- | --- | --- | --- |
| **WorkdayGetEducation** | Workday | custom engine agent | Answers a question for the signed-in employee | `msdyn_HRWorkdayHCMEmployeeGetEducation` |

### Topic

In Copilot Studio: **Topics > Add a topic > From blank**, name it **Get Education**, open **⋯ > Open code editor**, select all, paste this, and save.

```yaml
kind: AdaptiveDialog
modelDescription: |-
  Use this topic when the employee asks about their education history (school, degree, field of study) in Workday.

  Triggers: "What is my education?", "Show my education details", "What was my field of study?", "Which degree do I have on file?".

  Data belongs exclusively to the requesting user; never return or act on another person's data.

  Do NOT trigger for:
  - another person's education
  - adding or updating education records
  - training or learning courses
beginDialog:
  kind: OnRecognizedIntent
  id: main
  intent:
    triggerQueries:
      - "What is my education?"
      - "Show my education details"
      - "What was my field of study?"
      - "Which degree do I have on file?"

  actions:
    - kind: BeginDialog
      id: call_workday
      displayName: Workday common execution
      input:
        binding:
          parameters: |-
            ="{""params"":[" &
            "{""key"":""{Employee_ID}"",""value"":""" & Global.ESS_UserContext_Employee_Id & """}" &
            "]}"
          scenarioName: msdyn_HRWorkdayHCMEmployeeGetEducation

      dialog: msdyn_copilotforemployeeselfservicehr.topic.WorkdaySystemGetCommonExecution
      output:
        binding:
          errorResponse: Topic.errorResponse
          isSuccess: Topic.isSuccess
          workdayResponse: Topic.workdayResponse

    - kind: ConditionGroup
      id: on_result
      conditions:
        - id: succeeded
          condition: =Topic.isSuccess = true
          actions:
            - kind: AnswerQuestionWithAI
              id: answer_from_response
              autoSend: false
              variable: Topic.Answer
              userInput: =Topic.workdayResponse
              additionalInstructions: "Answer only what the user asked about, from these fields: School, Degree, FieldOfStudy. Use a short list with each field name in bold. Don't show internal IDs or citations, and don't add data that isn't in the response. Tie the answer to the user's question ({System.Activity.Text})."

      elseActions:
        - kind: SendActivity
          id: send_failure
          activity: "I couldn't get your education in Workday right now. Please try again later, or contact HR support if it keeps happening."

inputType: {}

outputType:
  properties:
    Answer:
      displayName: Answer
      type: String
```

### Template configuration

Add a row to the **Employee Self Service Template Configuration** table with unique name `msdyn_HRWorkdayHCMEmployeeGetEducation` and this value. The topic finds it by that name.

```xml
<workdayEntityConfigurationTemplate>
  <scenario name="GetEducation">
    <apiRequests>
      <apiRequest>
        <authType>User</authType>
        <endpoint>
          <request>msdyn_HRWorkdayHCMEmployeeGetEducation</request>
          <serviceName>Human_Resources</serviceName>
          <version>v42.0</version>
        </endpoint>
        <requestParameters>
          <parameter>
            <name>Employee_ID</name>
            <value>{Employee_ID}</value>
          </parameter>
        </requestParameters>
        <responseProperties>
          <property>
            <key>School</key>
            <extractPath>//*[local-name()='School_Name']/text()</extractPath>
          </property>
          <property>
            <key>Degree</key>
            <extractPath>//*[local-name()='Degree_Reference']/text()</extractPath>
          </property>
          <property>
            <key>FieldOfStudy</key>
            <extractPath>//*[local-name()='Field_Of_Study_Reference']/text()</extractPath>
          </property>
        </responseProperties>
      </apiRequest>
    </apiRequests>
  </scenario>
  <requestTemplates>
    <requestTemplate name="msdyn_HRWorkdayHCMEmployeeGetEducation">
      <bsvc:Get_Workers_Request xmlns:bsvc="urn:com.workday/bsvc" bsvc:version="v42.0">
        <bsvc:Request_References>
          <bsvc:Employee_Reference>
            <bsvc:ID bsvc:type="Employee_ID">{Employee_ID}</bsvc:ID>
          </bsvc:Employee_Reference>
        </bsvc:Request_References>

      </bsvc:Get_Workers_Request>
    </requestTemplate>
  </requestTemplates>
</workdayEntityConfigurationTemplate>
```

### Check before importing

- Check the request body and the XPath of each response field against the Workday operation.
- The topic calls `msdyn_copilotforemployeeselfservicehr.topic.…` system topics; in another agent, change that prefix to match the agent's ESS solution.

### Topic check

✅ The generated topic passes every topic rule.

### Files

- `outputs/topic/topic.yaml`: the topic above.
- `outputs/topic/msdyn_HRWorkdayHCMEmployeeGetEducation.xml`: the template configuration record.
- `outputs/topic/SKILL.md`: the same scenario as an agent skill, for when it moves from a topic to a skill.
- `outputs/topic/evalset.csv`: 7 test questions (the trigger phrases, and what the topic must not handle). Import it in Copilot Studio: open the agent > **Evaluation** > **Create a test set** (or **New evaluation**) > upload the CSV, then run it before and after publishing the change.


<!-- essdoctor: version=2.12.1 skill=generate-workflows command=topic-gen -->
