import logging

# variables:
# - users: [string -> User]
# - roles: [string -> Role]
# - privs: [string -> Priv]
# - privmap: [string -> PrivRef]
# types:
# - Targ:
#   - name: string
#   - ptype: string
#   - patterns: [string]
#   - defincpat: [string]
#   - defexcpat: [string]
# - User:
#   - username: string
#   - email: string
#   - enabled: bool
#   - realm: bool
#   - roles: [Role]
#   - builtin: bool
# - Role:
#   - groupName: string
#   - description: string
#   - admin: bool
#   - roles: [Role]
#   - privileges: [PrivRef]
#   - builtin: bool
# - Priv:
#   - name: string
#   - repo: string
#   - ptype: string
#   - patterns: [string]
#   - defincpat: [string]
#   - defexcpat: [string]
#   - builtin: bool
# - PrivRef:
#   - id: string
#   - type: string
#   - repo: string (only for type 'view')
#   - priv: Priv (only for type 'target')
#   - method: string (only for types 'application' and 'target')
#   - permission: string (only for type 'application')
#   - needadmin: bool

class Security3(object):
    def __init__(self):
        self.log = logging.getLogger(__name__)
        self.adminmethods = set(["ldap", "licensing", "logging", "privileges",
        "roles", "selectors", "settings", "tasks", "users", "*"])
        self.initialize()

    def initialize(self):
        self.users = None
        self.roles = None
        self.privs = None
        self.privmap = None

    def refresh(self, data):
        self.log.info("Reading security config from Nexus.")
        try:
            targs, privs, privmap, roles, users = {}, {}, {}, {}, {}
            for targ in data['selectors']:
                targdata = self.gettarget(targ)
                targs[targdata['name']] = targdata
            for priv in data['privs']:
                privdata = self.getprivilege(priv, targs)
                privmap[privdata['id']] = privdata
                if 'priv' in privdata:
                    privs[privdata['priv']['name']] = privdata['priv']
            ldaproles = set()
            for role in data['groups']:
                roledata = self.getrole(role, privmap)
                if role['source'] == 'LDAP':
                    ldaproles.add(roledata['groupName'])
                    continue
                roles[roledata['groupName']] = roledata
            # TODO deal with autogenerated permissions
            # TODO consolidate permissions when possible
            for role in roles.values(): self.flattenrole(role, roles)
            for user in data['users']:
                userdata = self.getuser(user, roles)
                if userdata['username'] == 'anonymous': continue
                if user['source'] == 'LDAP':
                    rs = map(lambda x: x['groupName'], userdata['roles'])
                    if ldaproles >= set(rs): continue
                users[userdata['username']] = userdata
            self.log.info("Successfully read security config.")
            self.users = users
            self.roles = roles
            self.privs = privs
            self.privmap = privmap
            return True
        except:
            self.log.exception("Error reading security config:")
            return "Error reading security config."

    def flattenrole(self, role, roles):
        while len(role['roles']) > 0:
            child = role['roles'].pop()
            if child not in roles: continue
            privs = self.flattenrole(roles[child], roles)
            if roles[child]['admin']: role['admin'] = True
            for priv in privs:
                if priv not in role['privileges']:
                    role['privileges'].append(priv)
        return role['privileges']

    def gettarget(self, targ):
        targdata = {'defincpat': False, 'defexcpat': False}
        targdata['name'] = targ['name']
        targdata['patterns'] = [targ['attributes']['expression']]
        # TODO parse the selector and handle some simple combinations
        return targdata

    def getuser(self, user, groups):
        userdata = {}
        userdata['username'] = user['id']
        userdata['email'] = user['email']
        userdata['enabled'] = user['status'] == 'active'
        userdata['realm'] = user['source'].lower()
        if userdata['realm'] == 'default': userdata['realm'] = 'internal'
        roles = []
        for role in user['roles']:
            if role['id'] in groups: roles.append(groups[role['id']])
        userdata['roles'] = roles
        userdata['builtin'] = user['readonly']
        return userdata

    def getrole(self, role, privmap):
        roledata = {'admin': role['id'] == 'nx-admin'}
        roledata['groupName'] = role['id']
        roledata['description'] = role['description']
        privs, roles = [], []
        if role['privileges'] != None:
            for priv in role['privileges']:
                if not priv.startswith('nx-repository-') or priv in privmap:
                    privs.append(privmap[priv])
        roledata['privileges'] = privs
        for roleid in role['roles']: roles.append(roleid)
        roledata['roles'] = roles
        roledata['builtin'] = role['readonly']
        return roledata

    def getprivilege(self, priv, targs):
        privdata, privref = {}, {}
        privdata['name'] = priv['name']
        privdata['builtin'] = priv['readonly']
        privref['id'] = priv['id']
        privref['needadmin'] = False
        if priv['type'] == 'application':
            privref['type'] = 'application'
            privref['permission'] = priv['properties']['domain']
            privref['method'] = priv['properties']['actions']
            if privref['permission'] in self.adminmethods:
                privref['needadmin'] = True
        elif priv['type'] == 'repository-admin':
            privref['type'] = 'application'
            privref['permission'] = 'repoadmin-'
            privref['permission'] += priv['properties']['format'] + '-'
            privref['permission'] += priv['properties']['repository']
            privref['method'] = priv['properties']['actions']
            privref['needadmin'] = True
        elif priv['type'] == 'repository-view':
            privdata['repo'] = priv['properties']['repository']
            privdata['ptype'] = priv['properties']['format']
            privdata['patterns'] = ['path =~ ".*"']
            privdata['defincpat'] = ["**"]
            privdata['defexcpat'] = []
            privref['priv'] = privdata
            privref['method'] = self.getmethods(priv['properties']['actions'])
            privref['type'] = 'target'
        elif priv['type'] == 'repository-content-selector':
            privdata['repo'] = priv['properties']['repository']
            privdata['ptype'] = '*'
            cs = priv['properties']['contentSelector']
            if cs in targs:
                privdata['patterns'] = targs[cs]['patterns']
                privdata['defincpat'] = targs[cs]['defincpat']
                privdata['defexcpat'] = targs[cs]['defexcpat']
            else:
                privdata['patterns'] = []
                privdata['defincpat'] = False
                privdata['defexcpat'] = False
            privref['priv'] = privdata
            privref['method'] = self.getmethods(priv['properties']['actions'])
            privref['type'] = 'target'
        elif priv['type'] == 'wildcard':
            results = self.handlewildcard(priv['perm'], targs)
            privref['type'] = results[0]
            if results[0] == 'application':
                privref['needadmin'] = results[1]
                privref['method'] = results[2]
            else:
                privdata['repo'] = results[1]['repo']
                privdata['ptype'] = results[1]['ptype']
                privdata['patterns'] = results[1]['patterns']
                privdata['defincpat'] = results[1]['defincpat']
                privdata['defexcpat'] = results[1]['defexcpat']
                privref['priv'] = privdata
                privref['method'] = results[1]['method']
            privref['permission'] = 'wildcard-' + priv['properties']['pattern']
        elif priv['type'] == 'script':
            privref['type'] = 'application'
            privref['permission'] = 'script-' + priv['properties']['name']
            privref['method'] = priv['properties']['actions']
        return privref

    def getmethods(self, actions):
        if actions == '*': return 'rwdnm'
        methodstr = ''
        actionlist = actions
        if isinstance(actions, basestring):
            actionlist = actions.split(',')
        if len(actionlist) > 0: methodstr += 'r'
        if 'add' in actionlist or 'edit' in actionlist: methodstr += 'w'
        if 'delete' in actionlist or 'edit' in actionlist: methodstr += 'd'
        if 'w' in methodstr: methodstr += 'nm'
        return methodstr

    def handlewildcard(self, priv, targs):
        domains = [
            "analytics", "apikey", "atlas", "audit", "blobstores", "bundles",
            "capabilities", "healthcheck", "healthchecksummary", "ldap",
            "licensing", "metrics", "privileges", "roles", "search",
            "selectors", "settings", "ssl-truststore", "users", "userschangepw",
            "wonderland"
        ]
        formats = [
            "bower", "docker", "gitlfs", "maven2", "npm", "nuget", "pypi",
            "rubygems", "yum", "raw"
        ]
        crudacts = ["create", "read", "update", "delete"]
        breadacts = ["browse", "read", "edit", "add", "delete"]
        rcs = ['repository-content-selector']
        privtypes = [
            [['nexus'], domains, crudacts],
            [['nexus'], ['logging'], crudacts + ["mark"]],
            [['nexus'], ['tasks'], crudacts + ["start", "stop"]],
            [['nexus'], ['script'], ['*'], breadacts + ["run"]],
            [['nexus'], ['repository-admin'], formats, ['*'], breadacts],
            [['nexus'], ['repository-view'], formats, ['*'], breadacts],
            [['nexus'], rcs, targs.keys(), formats, ['*'], breadacts]
        ]
        alldomains = set(domains + ["logging", "tasks"])
        admin, privs, methods = False, [], set([])
        for wc in privtypes:
            p = self.wcintersection(wc, priv)
            if p == None: continue
            # TODO support multiple repos, multiple ptypes, multiple targets
            elif 'repository-content-selector' in p[1]:
                privdata = {}
                privdata['repo'] = p[4][0]
                privdata['ptype'] = p[3][0]
                if len(priv) < 3 or '*' in priv[2]:
                    privdata['patterns'] = ['path =~ ".*"']
                    privdata['defincpat'] = ["**"]
                    privdata['defexcpat'] = []
                elif p[2][0] in targs:
                    ts = targs[p[2][0]]
                    privdata['patterns'] = ts['patterns']
                    privdata['defincpat'] = ts['defincpat']
                    privdata['defexcpat'] = ts['defexcpat']
                else:
                    privdata['patterns'] = []
                    privdata['defincpat'] = False
                    privdata['defexcpat'] = False
                privdata['method'] = self.getmethods(p[5])
                privs.append(privdata)
                if len(priv) < 6 or '*' in priv[5]: methods = set(['*'])
                else: methods |= p[5]
            elif 'repository-view' in p[1]:
                privdata = {}
                privdata['repo'] = p[3][0]
                privdata['ptype'] = p[2][0]
                privdata['patterns'] = ['path =~ ".*"']
                privdata['defincpat'] = ["**"]
                privdata['defexcpat'] = []
                privdata['method'] = self.getmethods(p[4])
                privs.append(privdata)
                if len(priv) < 5 or '*' in priv[4]: methods = set(['*'])
                else: methods |= p[4]
            elif 'repository-admin' in p[1]:
                admin = True
                if len(priv) < 5 or '*' in priv[4]: methods = set(['*'])
                else: methods |= p[4]
            elif 'script' in p[1]:
                if len(priv) < 4 or '*' in priv[3]: methods = set(['*'])
                else: methods |= p[3]
            elif not alldomains.isdisjoint(p[1]):
                if not self.adminmethods.isdisjoint(p[1]): admin = True
                if len(priv) < 3 or '*' in priv[2]: methods = set(['*'])
                else: methods |= p[2]
        if '*' in methods: methods = '*'
        else: methods = ','.join(sorted(list(methods)))
        if admin or len(privs) <= 0: return 'application', admin, methods
        else: return 'target', privs[0]

    def wcintersection(self, wc1, wc2):
        inter = []
        for idx in xrange(max(len(wc1), len(wc2))):
            try: u = wc1[idx]
            except: u = ['*']
            try: v = wc2[idx]
            except: v = ['*']
            if '*' in u and '*' in v: w = ['*']
            elif '*' in u: w = v
            elif '*' in v: w = u
            else: w = list(set(u) & set(v))
            if len(w) <= 0: return None
            inter.append(w)
        return inter
