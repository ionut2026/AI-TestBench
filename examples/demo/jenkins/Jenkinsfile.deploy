// OWNER-COMMISSIONED TEMPLATE, not an operational controller.
// Replace every <placeholder>. Starting this job authorizes instrument changes; restrict Build permission
// to instrument owners. Agents never start it. See GRF docs/WORKFLOWS.md and docs/DEPLOYMENT.md.
// Protected job configuration must bind source, catalog, request bytes/digest, endpoints and approval.
// Do not replace these fixed approval inputs with unrestricted build parameters or floating build aliases.
// Requires Python 3.11+, installed product/GRF deploy extra, Lockable Resources and external site credentials.

pipeline {
    agent { label '<agent-label>' }
    options {
        timeout(time: 3, unit: 'HOURS')
        timestamps()
        disableConcurrentBuilds()
        lock(resource: '<authoritative-instrument-resource>')
    }
    environment {
        GRF_APPROVED_REQUEST = '<protected-absolute-request-file>'
        GRF_APPROVED_REQUEST_SHA256 = '<owner-approved-64-lowercase-hex-digest>'
        GRF_SITE_CONFIG = '<external-site-config>'
        GRF_WORKFLOW_OUT = "results/workflow-${BUILD_NUMBER}"
    }
    stages {
        stage('Bounded deployment and acceptance') {
            steps {
                withCredentials([
                    usernamePassword(credentialsId: '<jenkins-api-credential-id>', usernameVariable: 'JENKINS_USER', passwordVariable: 'JENKINS_TOKEN'),
                    string(credentialsId: '<board-password-credential-id>', variable: 'BOARD_1_PASSWORD'),
                ]) {
                    script {
                        // GRF snapshots the request, plans every numeric build+digest before promotion,
                        // validates deployment records, applies selection and gates even a failed test run.
                        if (isUnix()) {
                            sh '''
                                python -m grf workflow deploy-and-test --yes \
                                  --request "$GRF_APPROVED_REQUEST" --request-sha256 "$GRF_APPROVED_REQUEST_SHA256" \
                                  --config "$GRF_SITE_CONFIG" --outdir "$GRF_WORKFLOW_OUT"
                            '''
                        } else {
                            bat '''
                                @echo off
                                python -m grf workflow deploy-and-test --yes --request "%GRF_APPROVED_REQUEST%" --request-sha256 "%GRF_APPROVED_REQUEST_SHA256%" --config "%GRF_SITE_CONFIG%" --outdir "%GRF_WORKFLOW_OUT%"
                            '''
                        }
                    }
                }
            }
        }
    }
    post {
        always {
            archiveArtifacts artifacts: 'results/workflow-*/workflow.jsonl, results/workflow-*/request.toml, results/workflow-*/**/deployment.json, results/workflow-*/**/deploy.log, results/workflow-*/**/output.xml, results/workflow-*/acceptance.json, results/workflow-*/**/traceability.*, results/workflow-*/**/*.html',
                             allowEmptyArchive: true
        }
    }
}
