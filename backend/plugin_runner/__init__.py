"""The runner for plugins with code.

Plugin code never runs in the backend, the Celery workers or the browser. It
runs here, in its own container (Compose profile `plugins`) that holds none of
the core's secrets and has no route to the database, Redis or the internet:

* the backend asks the runner, over an internal network and with a shared
  token, to install a plugin or call one of its methods;
* each call is a fresh subprocess of the plugin, under its own Unix user, with
  only the standard library, the plugin's own source and the wheels its
  lockfile pins on the import path;
* the only way out is the egress proxy (`plugin_runner.egress`), which lets a
  plugin reach exactly the hosts its manifest declares, and nothing on the
  private network.

The runner imports nothing from `app`: it must not be able to read the core's
configuration, and it must start without a database.
"""
