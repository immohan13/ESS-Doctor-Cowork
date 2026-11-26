## Generate workflows

**Get Education**: a Workday topic for a custom engine agent. ✅ Passes every ESS topic rule.

| Topic | Does | For | ESS scenario |
| --- | --- | --- | --- |
| **WorkdayGetEducation** | Answers a question | the signed-in employee only | `msdyn_HRWorkdayHCMEmployeeGetEducation` |

### Step 1: Add the topic

1. In Copilot Studio, go to **Topics > Add a topic > From blank** and name it **Get Education**.
2. Open **⋯ > Open code editor**, select everything, paste the code below and save.

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

### Step 2: Add the template configuration

1. Open the **Employee Self Service Template Configuration** table and add a row.
2. Set **Unique name** to `msdyn_HRWorkdayHCMEmployeeGetEducation`. The topic finds the row by this name.
3. Paste the code below as the **Value** and save.

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

### Step 3: Confirm these details

Filled in with likely values; only you can confirm them for your tenant.

- [ ] Check the request body and the XPath of each response field against the Workday operation.
- [ ] The topic calls `msdyn_copilotforemployeeselfservicehr.topic.…` system topics; in another agent, change that prefix to match the agent's ESS solution.

### Step 4: Test it

`evalset.csv` has **7 questions**: the trigger phrases, plus questions the topic must not answer.

1. In Copilot Studio, open the agent > **Evaluation** > **Create a test set** (or **New evaluation**) and upload the CSV.
2. Run it on the current version.
3. Add the topic, publish, and run it again.
4. Upload both results files to ESS Doctor to compare them.

### Files

| File | What it is |
| --- | --- |
| `outputs/topic/topic.yaml` | The topic (step 1) |
| `outputs/topic/msdyn_HRWorkdayHCMEmployeeGetEducation.xml` | The template configuration row (step 2) |
| `outputs/topic/SKILL.md` | The same scenario as an agent skill, for when topics move to skills |
| `outputs/topic/evalset.csv` | The test questions (step 4) |

### What next

Reply with one of these:

- **"generate another topic"**: a new scenario, built the same way.
- **"check my existing topics"**: upload a test pane snapshot or bot content export.
- **"make it a declarative agent topic"**: the same topic for a declarative agent.


<!-- essdoctor: version=2.13.0 skill=generate-workflows command=topic-gen -->
