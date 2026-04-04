# Create your first show

This guide will walk you through creating your first show with ShowRunner, including setting up the database, starting the server, and creating a show.

We will create a production of Shakespeare's _Hamlet_ using the built-in plugins for script parsing, cue management, and show control.

If you haven't already, follow the [Getting Started](getting-started.md) guide to install ShowRunner and its dependencies.

## Step 1 – Create a new show

```bash
sr create Hamlet --venue "Globe Theatre"
```

## Step 2 – Import the script

Import the script using the `scripts add` command:

```bash
sr scripts add 1 "Hamlet Script" --format fountain --file ./examples/scripts/Hamlet.fountain
```

## Step 3 – Start the server

```bash
sr start
```

Visit [http://localhost:8000](http://localhost:8000) to see the dashboard.

Your new show should be selected and visible in the sidebar. You can click on it to see the show details, including the imported script.

## Step 4 – Create cue lists and cues

Visit [/script](http://localhost:8000/script) to see the parsed script.

Select a cue layer from the toolbar, and click anywhere on the script text to create a cue at that point. The cue will be added to the selected cue layer and visible in the sidebar.

You can then edit the cue details, such as the name and notes.

You can view and manage all cues in the from the admin panel at [/admin](http://localhost:8000/admin) (requires `admin` group to be installed).
