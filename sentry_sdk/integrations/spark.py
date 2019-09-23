from __future__ import absolute_import

from sentry_sdk import configure_scope, capture_exception
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration

SCOPE_TAGS = frozenset(("startTime"))

class SparkIntegration(Integration):
    identifier = "spark"

    @staticmethod
    def setup_once():
        pass
        # type: () -> None
        patch_pyspark_java_gateway()
        patch_spark_context()
        track_spark_workers()
        patch_spark_context_getOrCreate()


def track_spark_workers():
    from pyspark.worker import main
    old_main = main

    def _sentry_wrapped_main(*args, **kwargs):
        try:
            old_main(*args, **kwargs)
        except Exception as e:
            capture_exception(e)
    main = _sentry_wrapped_main


def patch_spark_context_getOrCreate():
    from pyspark import SparkContext
    from pyspark.java_gateway import ensure_callback_server_started

    spark_context_getOrCreate = SparkContext.getOrCreate

    def _sentry_patched_spark_context_getOrCreate(self, *args, **kwargs):
        sc = spark_context_getOrCreate(self, *args, **kwargs)
        
        gw = sc._gateway
        ensure_callback_server_started(gw)
        listener = SentryListener()
        sc._jsc.sc().addSparkListener(listener)
        return sc

    SparkContext.getOrCreate = _sentry_patched_spark_context_getOrCreate


def patch_spark_context():
    from pyspark import SparkContext # type: ignore

    spark_context_init = SparkContext._do_init

    def _sentry_patched_spark_context_init(self, *args, **kwargs):
        spark_context_init(self, *args, **kwargs)

        with configure_scope() as scope:
            scope.set_tag("user", self.sparkUser())
            scope.set_tag("spark_version", self.version)
            scope.set_tag("app_name", self.appName)
            scope.set_tag("application_id", self.applicationId)

            scope.set_extra("start_time", self.startTime)
            scope.set_extra("web_url", self.uiWebUrl)

    SparkContext._do_init = _sentry_patched_spark_context_init


def patch_pyspark_java_gateway():
    from py4j.java_gateway import java_import
    from pyspark.java_gateway import launch_gateway

    old_launch_gateway = launch_gateway

    def _sentry_patched_launch_gateway(self, *args, **kwargs):
        gateway = old_launch_gateway(self, *args, **kwargs)
        java_import(gateway.jvm, "org.apache.spark.scheduler")
        return gateway

    launch_gateway = _sentry_patched_launch_gateway


class SparkListener(object):
    def onApplicationEnd(self, applicationEnd):
        pass
    def onApplicationStart(self, applicationStart):
        pass
    def onBlockManagerRemoved(self, blockManagerRemoved):
        pass
    def onBlockUpdated(self, blockUpdated):
        pass
    def onEnvironmentUpdate(self, environmentUpdate):
        pass
    def onExecutorAdded(self, executorAdded):
        pass
    def onExecutorMetricsUpdate(self, executorMetricsUpdate):
        pass
    def onExecutorRemoved(self, executorRemoved):
        pass
    def onJobEnd(self, jobEnd):
        pass
    def onJobStart(self, jobStart):
        pass
    def onOtherEvent(self, event):
        pass
    def onStageCompleted(self, stageCompleted):
        pass
    def onStageSubmitted(self, stageSubmitted):
        pass
    def onTaskEnd(self, taskEnd):
        pass
    def onTaskGettingResult(self, taskGettingResult):
        pass
    def onTaskStart(self, taskStart):
        pass
    def onUnpersistRDD(self, unpersistRDD):
        pass
    class Java:
        implements = ["org.apache.spark.scheduler.SparkListenerInterface"]


class SentryListener(SparkListener):
    def onTaskEnd(self, taskEnd):
        hub = Hub.current
        print({"stageId": 'hellooo!!'})
        data = {"reason": taskEnd.reason}
        hub.add_breadcrumb(level="info", message="hello!!!", data=data)
        print(data)
