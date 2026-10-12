{
  "specifications": [
    {
      "ID": 1,
      "Type": "Specification",
      "State": "Accepted",
      "Project": "/Demo",
      "Text": "When increment is requested, the counter increases by exactly one.",
      "Satisfies": ["2 (Requirement) Count requests [Accepted]"],
      "Described In": ["3 (ASD-User Story) Observe increments [Tested]"],
      "Relevant Explorative Test": ["4 (Explorative Test) Increment once [Accepted]"]
    }
  ],
  "requirements": [
    {"ID": 2, "State": "Accepted", "Text": "The counter shall count increment requests."}
  ],
  "userStories": [
    {"ID": 3, "State": "Tested", "Summary": "Observe increments", "Description": "Show the incremented value."}
  ],
  "explorativeTests": [
    {
      "ID": 4,
      "State": "Accepted",
      "Summary": "Increment once",
      "Description": "Preconditions:\nCounter is zero.\nSteps:\nRequest an increment.\nExpected Results:\nCounter is one."
    }
  ]
}
