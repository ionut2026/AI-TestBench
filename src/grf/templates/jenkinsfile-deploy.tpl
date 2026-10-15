// Jenkins pipeline TEMPLATE: deploy Jenkins builds to the instrument boards, then run the tests against them.
// Guide: docs/DEPLOYMENT.md in GeneralRobotFramework. Replace every <placeholder> before use.
//
// Starting this job IS the instrument owner's authorization to change the instrument: give the Jenkins "Build"
// permission of this job only to the instrument owners. Agents never start it.
//
// Prerequisites on the site agent (<agent-label>): Python 3.11+, this repository installed with
// `pip install -e .` and GRF's deploy extra (`general-robot-framework[deploy]`), network access to Jenkins and the
// boards, and the site config (deploy.example.toml filled in) OUTSIDE the workspace.

def runCommand(String command) {
    if (isUnix()) { sh command } else { bat command }
}

pipeline {
    agent { label '<agent-label>' }

    parameters {
        string(name: 'COMPONENTS', defaultValue: '<component>', description: 'Component(s) to deploy, separated by commas')
        string(name: 'BUILD', defaultValue: 'lastSuccessfulBuild', description: 'Jenkins build number or lastSuccessfulBuild')
        string(name: 'BRANCH', defaultValue: '', description: 'Branch of a multibranch job (empty: [deploy] default_branch)')
        booleanParam(name: 'PLAN_ONLY', defaultValue: false, description: 'Dry run: download and check, change nothing')
        booleanParam(name: 'RUN_TESTS', defaultValue: true, description: 'Run the tests after a successful deployment')
        string(name: 'TIER', defaultValue: '<tier>', description: 'grf run --tier after the deployment')
    }

    // Nightly: deploy lastSuccessfulBuild, then test. Remove if the site does not want it.
    triggers { cron('H 2 * * *') }

    options {
        timeout(time: 3, unit: 'HOURS')
        timestamps()
        disableConcurrentBuilds()
        // With the Lockable Resources plugin, also serialise every job that touches the instrument:
        // lock(resource: '<instrument-name>')
    }

    environment {
        GRF_DEPLOY_CONFIG = '<path to the site config outside the workspace>'
    }

    stages {
        stage('Deploy') {
            steps {
                script {
                    dir('results/deploy') { deleteDir() }  // no stale records from an earlier run in the report
                    def trigger = currentBuild.getBuildCauses('hudson.triggers.TimerTrigger$TimerTriggerCause') ? 'nightly' : 'manual'
                    def branch = params.BRANCH ? "--branch \"${params.BRANCH}\"" : ''
                    def action = params.PLAN_ONLY ? 'plan' : 'run --yes'
                    withCredentials([
                        usernamePassword(credentialsId: '<jenkins-api-credential-id>', usernameVariable: 'JENKINS_USER', passwordVariable: 'JENKINS_TOKEN'),
                        string(credentialsId: '<board-password-credential-id>', variable: 'BOARD_1_PASSWORD'),
                    ]) {
                        runCommand("grf deploy ${action} --component \"${params.COMPONENTS}\" --build \"${params.BUILD}\" ${branch} " +
                                   "--trigger ${trigger} --outdir results/deploy")
                    }
                }
            }
        }
        stage('Test') {
            when { expression { params.RUN_TESTS && !params.PLAN_ONLY } }
            steps {
                // --deployment puts the deployed builds into the traceability report.
                runCommand("grf run --tier \"${params.TIER}\" --deployment results/deploy")
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'results/deploy/**/deployment.json, results/deploy/**/deploy.log, results/**/traceability.*, results/**/*.html',
                             allowEmptyArchive: true
        }
    }
}
