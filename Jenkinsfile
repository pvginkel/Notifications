// Tests the notification platform's apps in a Kubernetes Job, then builds the notification-bridge
// image and pins it into NotificationsDeploy's prd values.
//
// The image is built from the tree the suite passed on, so `latest` is tagged at build time and
// there is no promote stage.
//
// Controller config:
//   - Job: Notifications
//   - SCM: pvginkel/Notifications, branch main
//   - Script Path: Jenkinsfile

library identifier: 'JenkinsPipelineUtils', changelog: false

pipeline {
    agent {
        kubernetes {
            inheritFrom 'jenkins-agent kaniko'
            yamlMergeStrategy merge()
            yaml podYaml(templates: ['k8s'])
        }
    }

    options {
        disableConcurrentBuilds(abortPrevious: true)
        skipDefaultCheckout()
        timeout(time: 60, unit: 'MINUTES')
        timestamps()
    }

    triggers {
        githubPush()
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Test') {
            steps {
                script {
                    // pytest is in the root's dev group and the apps install only with
                    // --all-packages. run-suite is a script at the repo root, not an installed
                    // command, so it runs under the workspace's Python.
                    modernApp.test(
                        job: 'notifications-validation',
                        install: 'uv sync --locked --all-packages',
                        run: 'uv run --no-sync python',
                        suites: ['pytest'],
                        services: [],
                        env: [:],
                        secrets: [],
                    )
                }
            }
        }

        stage('Build notification-bridge image') {
            steps {
                container('kaniko') {
                    script {
                        // The context is the repo root, where the workspace's uv.lock lives.
                        helmCharts.kaniko2(
                            dockerfile: 'bridge/Dockerfile',
                            context: '.',
                            destinations: [
                                "registry:5000/notification-bridge:${currentBuild.number}",
                                'registry:5000/notification-bridge:latest',
                            ]
                        )
                    }
                }
            }
        }

        stage('Write image pins') {
            steps {
                container('k8s') {
                    script {
                        cicd.writeVersionPins(repo: 'pvginkel/NotificationsDeploy', pins: [
                            'config/prd/values.yaml': [
                                'images.notificationBridge': ":${currentBuild.number}",
                            ],
                        ])
                    }
                }
            }
        }
    }
}
