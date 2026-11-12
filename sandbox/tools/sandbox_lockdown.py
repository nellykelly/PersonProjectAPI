"""dbt-duckdb plugin: lock down every connection dbt opens in the sandbox.

DuckDB only accepts these as runtime SETs, in this order (allowed_directories has to be
set while external access is still on, and can't go in connect-time config at all), so a
plugin's configure_connection hook, which runs once right after connect, is the place.
After this, models, `dbt show --inline`, macros and hooks can't read or write any file
outside the visitor's own seeds/ folder, can't INSTALL/LOAD extensions, can't reach the
network, and can't SET their way back out.
"""
from dbt.adapters.duckdb.plugins import BasePlugin


class Plugin(BasePlugin):
    def initialize(self, plugin_config):
        self.seeds_dir = plugin_config["seeds_dir"]

    def configure_connection(self, conn):
        escaped = self.seeds_dir.replace("'", "''")
        conn.execute(f"SET allowed_directories = ['{escaped}']")
        conn.execute("SET enable_external_access = false")
        conn.execute("SET memory_limit = '512MB'")
        conn.execute("SET lock_configuration = true")
