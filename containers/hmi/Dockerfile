# SCADA-LTS is the actively maintained community fork of ScadaBR.
# Project: https://github.com/SCADA-LTS/Scada-LTS
# Image:   https://hub.docker.com/r/scadalts/scadalts
#
# We pin a known-good tag rather than 'latest' for reproducibility of the
# experimental matrix.
FROM scadalts/scadalts@sha256:6d33e57ffd5d19a4babb2a1aea544bd36fa993e73dc3a6623dd676a313affc0a

# The upstream image already exposes 8080 and bundles Tomcat + the SCADA app.
# We override the JVM heap size to keep memory predictable during the
# multi-iteration matrix runs.
ENV CATALINA_OPTS="-Xms512m -Xmx1024m"

EXPOSE 8080
