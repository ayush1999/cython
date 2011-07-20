from TreeFragment import parse_from_strings, StringParseContext
from Scanning import StringSourceDescriptor
import Symtab
import Naming
from Cython.Compiler import Visitor

class NonManglingModuleScope(Symtab.ModuleScope):

    def __init__(self, prefix, *args, **kw):
        self.prefix = prefix
        Symtab.ModuleScope.__init__(self, *args, **kw)

    def add_imported_entry(self, name, entry, pos):
        entry.used = True
        return super(NonManglingModuleScope, self).add_imported_entry(
                                                        name, entry, pos)
    
    def mangle(self, prefix, name=None):
        if name:
            if prefix in (Naming.typeobj_prefix, Naming.func_prefix, Naming.var_prefix, Naming.pyfunc_prefix):
                # Functions, classes etc. gets a manually defined prefix easily
                # manually callable instead (the one passed to CythonUtilityCode)
                prefix = self.prefix
            return "%s%s" % (prefix, name)
        else:
            return Symtab.ModuleScope.mangle(self, prefix)

class CythonUtilityCodeContext(StringParseContext):
    scope = None

    def find_module(self, module_name, relative_to = None, pos = None,
                    need_pxd = 1):
        if module_name != self.module_name:
            raise AssertionError("Not yet supporting any cimports/includes "
                                 "from string code snippets")

        if self.scope is None:
            self.scope = NonManglingModuleScope(self.prefix,
                                                module_name,
                                                parent_module=None,
                                                context=self)

        return self.scope


class CythonUtilityCode(object):
    """
    Utility code written in the Cython language itself.

    The @cname decorator can set the cname for a function, method of cdef class.
    Functions decorated with @cname('c_func_name') get the given cname.

    For cdef classes the rules are as follows:
        obj struct      -> <cname>_obj
        obj type ptr    -> <cname>_type
        methods         -> <class_cname>_<method_cname>

    For methods the cname decorator is optional, but without the decorator the
    methods will not be prototyped. See Cython.Compiler.CythonScope and
    tests/run/cythonscope.pyx for examples.
    """

    is_cython_utility = True

    def __init__(self, impl, name="__pyxutil", prefix="", requires=None):
        # 1) We need to delay the parsing/processing, so that all modules can be
        #    imported without import loops
        # 2) The same utility code object can be used for multiple source files;
        #    while the generated node trees can be altered in the compilation of a
        #    single file.
        # Hence, delay any processing until later.
        self.pyx = impl
        self.name = name
        self.prefix = prefix
        self.requires = requires or []

    def get_tree(self, entries_only=False):
        from AnalysedTreeTransforms import AutoTestDictTransform
        # The AutoTestDictTransform creates the statement "__test__ = {}",
        # which when copied into the main ModuleNode overwrites
        # any __test__ in user code; not desired
        excludes = [AutoTestDictTransform]

        import Pipeline, ParseTreeTransforms
        context = CythonUtilityCodeContext(self.name)
        context.prefix = self.prefix
        #context = StringParseContext(self.name)
        tree = parse_from_strings(self.name, self.pyx, context=context)
        pipeline = Pipeline.create_pipeline(context, 'pyx', exclude_classes=excludes)

        if entries_only:
            p = []
            for t in pipeline:
                p.append(t)
                if isinstance(p, ParseTreeTransforms.AnalyseDeclarationsTransform):
                    break

            pipeline = p

        transform = ParseTreeTransforms.CnameDirectivesTransform(context)
        # InterpretCompilerDirectives already does a cdef declarator check
        #before = ParseTreeTransforms.DecoratorTransform
        before = ParseTreeTransforms.InterpretCompilerDirectives
        pipeline = Pipeline.insert_into_pipeline(pipeline, transform,
                                                 before=before)

        (err, tree) = Pipeline.run_pipeline(pipeline, tree)
        assert not err, err
        return tree

    def put_code(self, output):
        pass

    def declare_in_scope(self, dest_scope, used=False):
        """
        Declare all entries from the utility code in dest_scope. Code will only
        be included for used entries.
        """
        tree = self.get_tree(entries_only=True)

        entries = tree.scope.entries
        entries.pop('__name__')
        entries.pop('__file__')
        entries.pop('__builtins__')
        entries.pop('__doc__')

        for name, entry in entries.iteritems():
            entry.utility_code_definition = self
            entry.used = used

        dest_scope.merge_in(tree.scope, merge_unused=True)
        tree.scope = dest_scope

        for dep in self.requires:
            if dep.is_cython_utility:
                dep.declare_in_scope(dest_scope)
