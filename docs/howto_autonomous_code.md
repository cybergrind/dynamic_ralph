
# some preconditions

- LLMs are generating A LOT OF CODE for every small task and tends to rewrite a lot of things
  - and break working stuff
  - and forget what you've told just few minutes ago
- LLMs sometimes can assume that task is completed when it is failed (exit code = 0, but there is no actual build in the directory)
- LLMs can wait dozens of minutes, without trying to optimize and can repeat this a lot of times




# some solutions

- ask to write in red/green TDD manner. It shouldn't be only unit tests, you can go with bigger e2e tests, but you should have something CODED to check things
- often it is worth to implement some additional tools
- sometimes re-iterate over you tests and ask for optimize/remove old tests
- ask to measure timing for tests and steps, use sane timeouts and try to make things faster
