# PSScriptAnalyzer settings for install\ (run by CI at Warning).
@{
    Severity     = @('Error', 'Warning')
    ExcludeRules = @(
        # The installer talks to the person at the PC; its output is not data
        # another command pipes on.
        'PSAvoidUsingWriteHost',
        # Dry runs go through Invoke-GcChange (-DryRun), not -WhatIf/-Confirm
        # on every helper: one switch covers every step, and a test proves it.
        'PSUseShouldProcessForStateChangingFunctions'
    )
}
