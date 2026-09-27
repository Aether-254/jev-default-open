# Hook ShellExecute instead of claiming default associations

We will intercept eligible ShellExecute calls in memory because Windows requires default-app changes to be made through system UI, while the project must make a context-dependent choice on every open. This gives the demo the required control but limits support to verified desktop processes and requires strict failure-open behavior.
