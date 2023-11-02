{{/*
Get whether or not opensearch is local or remote.
*/}}
{{- define "malcolm.opensearchprimary" -}}
{{- if .Values.opensearch.enabled }}
{{- printf "%s" "opensearch-local" }}
{{- else if .Values.external_elasticsearch.enabled }}
{{- printf "%s" "elasticsearch-remote" }}
{{- else }}
{{- printf "%s" "opensearch-local" }}
{{- end }}
{{- end }}

{{/*
Get Opensearch or Elasticsearch url.
*/}}
{{- define "malcolm.opensearchprimaryurl" -}}
{{- if .Values.opensearch.enabled }}
    {{- printf "%s" .Values.opensearch.url }}
{{- else if .Values.external_elasticsearch.enabled }}
    {{- $url := .Values.external_elasticsearch.url }}
    {{- if .Values.external_elasticsearch.username }}
        {{- $parts := split "://" .Values.external_elasticsearch.url }}
        {{- $url := printf "%s://%s" $parts._0 .Values.external_elasticsearch.username }}
        {{- if .Values.external_elasticsearch.password }}
            {{- $url = printf "%s:%s" $url .Values.external_elasticsearch.password }}
        {{- end }}
        {{- $url = printf "%s@%s" $url $parts._1 }}
        {{- printf "%s" $url }}
    {{- else }}
        {{- printf "%s" $url }}
    {{- end }}
{{- else }}
    {{- printf "%s" .Values.opensearch.url }}
{{- end }}
{{- end }}


{{/*
Get Opensearch or Elasticsearch dashboards url. TODO figure out a way to refactor this so 
I am not duplicating this template code.
*/}}
{{- define "malcolm.dashboardsurl" -}}
{{- if .Values.opensearch.enabled }}
    {{- printf "%s" .Values.opensearch.dashboards_url }}
{{- else if .Values.external_elasticsearch.enabled }}
    {{- $url := .Values.external_elasticsearch.dashboards_url }}
    {{- if .Values.external_elasticsearch.username }}
        {{- $parts := split "://" .Values.external_elasticsearch.dashboards_url }}
        {{- $url := printf "%s://%s" $parts._0 .Values.external_elasticsearch.username }}
        {{- if .Values.external_elasticsearch.password }}
            {{- $url = printf "%s:%s" $url .Values.external_elasticsearch.password }}
        {{- end }}
        {{- $url = printf "%s@%s" $url $parts._1 }}
        {{- printf "%s" $url }}
    {{- else }}
        {{- printf "%s" $url }}
    {{- end }}
{{- else }}
    {{- printf "%s" .Values.opensearch.dashboards_url }}
{{- end }}
{{- end }}


{{/*
Get Opensearch or Elasticsearch url short version (IE: opensearch:9200). 
*/}}
{{- define "malcolm.primaryurlshort" -}}
{{- if .Values.external_elasticsearch.enabled }}    
    {{- $parts := split "://" .Values.external_elasticsearch.url }}
    {{- printf "%s" $parts._1 }}
{{- else }}    
    {{- $parts := split "://" .Values.opensearch.url }}
    {{- printf "%s" $parts._1 }}
{{- end }}
{{- end }}


{{/*
Get Opensearch or Elasticsearch dashboards url short version (IE: dashboards:5601). 
*/}}
{{- define "malcolm.dashboardsurlshort" -}}
{{- if .Values.external_elasticsearch.enabled }}    
    {{- $parts := split "://" .Values.external_elasticsearch.dashboards_url }}
    {{- printf "%s" $parts._1 }}
{{- else }}    
    {{- $parts := split "://" .Values.opensearch.dashboards_url }}
    {{- $parts2 := split "/" $parts._1 }}
    {{- printf "%s" $parts2._0 }}
{{- end }}
{{- end }}


{{/*
Used for secret generation for the opensearch-curlrc Kubernetes secret
*/}}
{{- define "malcolm.curlrc" -}}
{{- if .Values.external_elasticsearch.username }}
    {{- if .Values.external_elasticsearch.password }}
        {{- printf "--user %s:%s " .Values.external_elasticsearch.username .Values.external_elasticsearch.password | b64enc | quote }}
    {{- else }}
        {{- printf "--user %s " .Values.external_elasticsearch.username | b64enc | quote }}
    {{- end }}
{{- else }}
    {{- printf "" }}
{{- end }}
{{- end }}


{{/*
Expand the name of the chart.
*/}}
{{- define "malcolm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "malcolm.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "malcolm.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "malcolm.labels" -}}
helm.sh/chart: {{ include "malcolm.chart" . }}
{{ include "malcolm.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "malcolm.selectorLabels" -}}
app.kubernetes.io/name: {{ include "malcolm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "malcolm.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "malcolm.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
